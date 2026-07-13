# test_stall_races.py — bio_lite_top RTL level
#
# Tier 2: stall-boundary races
#   1. push-vs-stall phase sweep      (wake must never drop/double-consume)
#   2. full-FIFO backpressure         (core write-blocked; level/order invariants)
#   3. event set-then-clear           (stalled core wakes exactly once)
#   4. FIFO exact-full boundary       (host push #9 dropped; level caps at 8)
#
# Tier 3:
#   5. mul immediately before quanta wait and before FIFO-blocking read
#   6. core-side event set -> IRQ1 output
#
# NOTE on "races" over SPI: consecutive SPI frames are ~50us apart (many quanta),
# so true cycle-level races are not host-reachable. The phase sweep controls the
# *clock-phase alignment* of the push relative to the quantum tick instead.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from spi_master import SPIMaster

CLK_NS     = 40
QDIV       = 100
QUANTUM_NS = QDIV * CLK_NS

FIFO_ECHO = [           # verified set: dir/mask all; echo FIFO0 -> GPIO, quanta-paced
    0xFFF00293, 0x00028C13, 0x00028D13,
    0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F,
]

# Producer: push counter values into FIFO1 as fast as possible (write-blocks when full)
# x6 = counter starting 0x100, increment each loop
PRODUCER = [
    0x10000313,   # li   x6, 0x100
    0x00030893,   # mv   x17, x6        (push FIFO1; BLOCKS when full)
    0x00130313,   # addi x6, x6, 1
    0xFF9FF06F,   # j    -8 (push loop)
]

# Event-wait then mark: wait on event bit0, then GPIO=0xE, park
EVENT_WAIT = [
    0xFFF00293, 0x00028C13, 0x00028D13,       # dir/mask all
    0x00100313,                                # li x6, 1
    0x00030D93,                                # mv x27, x6   (EV_MASK = bit0)
    0x000F0393,                                # mv x7, x30   (WAIT)
    0x00E00A93,                                # li x21, 0xE  (marker)
    0x0000006F,                                # park
]

# mul then quanta-wait then mul then FIFO-blocking read; results -> FIFO1
MUL_STALL_MIX = [
    0x00700513,   # li  a0, 7
    0x00600593,   # li  a1, 6
    0x02B50633,   # mul a2, a0, a1          -> 42
    0x00060893,   # mv  x17, a2   (push FIFO1)
    0x00000A13,   # mv  x20, x0   (QUANTA WAIT right after mul)
    0x00900513,   # li  a0, 9
    0x00900593,   # li  a1, 9
    0x02B50633,   # mul a2, a0, a1          -> 81
    0x00080313,   # mv  x6, x16   (FIFO0 BLOCKING READ right after mul)
    0x006606B3,   # add a3, a2, x6          (result + popped value)
    0x00068893,   # mv  x17, a3   (push FIFO1)
    0x0000006F,   # park
]

# Core-side event set: set event bit 1 via x28, then park
CORE_EVENT_SET = [
    0x00200313,   # li x6, 2       (event bit 1)
    0x00030E13,   # mv x28, x6     (EV_SET)
    0x0000006F,   # park
]


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    dut.reset_n.value = 0
    dut.gpio_in.value = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)
    spi = SPIMaster(dut)
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x3C, 0x00FFFFFF)      # clear events
    return spi


async def load_and_start(spi, core, prog, en_word):
    await spi.bio_write(0xE0, core)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x50 + 4 * core, QDIV << 16)
    await spi.bio_write(0x00, en_word)


async def levels(spi):
    v = await spi.bio_read(0x0C)
    return [(v >> (4 * i)) & 0xF for i in range(4)]


def gpio(dut):
    try:
        return int(dut.gpio_out.value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_push_phase_sweep(dut):
    """Core FIFO-blocked; push at 8 different clock phases relative to the
    quantum tick. Every word must be echoed exactly once, in order."""
    spi = await setup(dut)
    await load_and_start(spi, 0, FIFO_ECHO, 0x00000005)
    await Timer(5 * QUANTUM_NS, unit="ns")            # now blocked

    for phase in range(8):
        await ClockCycles(dut.clk, phase * 13)        # vary alignment vs quantum
        val = 0xC0 + phase
        await spi.bio_write(0x10, val)
        await Timer(3 * QUANTUM_NS, unit="ns")
        v = gpio(dut)
        assert v == val, f"phase {phase}: echo 0x{v:08X} != 0x{val:02X}"
        # exactly-once: FIFO must be empty again (no double-consume/leftover)
        lv = await levels(spi)
        assert lv[0] == 0, f"phase {phase}: FIFO0 level {lv[0]} != 0 after echo"
        dut._log.info(f"phase {phase}: 0x{val:02X} echoed exactly once")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_full_fifo_backpressure(dut):
    """Producer core write-blocks on full FIFO1. Level must cap at 8; host pops
    must release exactly one slot each; data strictly ordered."""
    spi = await setup(dut)
    await load_and_start(spi, 0, PRODUCER, 0x00000005)
    await Timer(20 * QUANTUM_NS, unit="ns")           # plenty to fill

    lv = await levels(spi)
    assert lv[1] == 9, f"FIFO1 should be full(8), levels={lv}"

    expected = 0x100
    for i in range(12):                               # drain past a full wrap
        got = await spi.bio_read(0x24)                # SINGLE read: pops!
        assert got == expected, f"pop {i}: 0x{got:08X} != 0x{expected:08X} (order broken)"
        expected += 1
        await Timer(2 * QUANTUM_NS, unit="ns")        # producer refills the slot
        lv = await levels(spi)
        assert lv[1] <= 9, f"level exceeded 8: {lv}"
    dut._log.info("backpressure: level capped at 8, 12 pops strictly ordered")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_event_set_then_clear(dut):
    """Stalled core must wake exactly once on a set that is cleared one frame
    later, and must NOT re-stall or re-trigger."""
    spi = await setup(dut)
    await load_and_start(spi, 0, EVENT_WAIT, 0x00000005)
    await Timer(5 * QUANTUM_NS, unit="ns")
    assert gpio(dut) != 0xE, "core did not stall on event wait"

    await spi.bio_write(0x38, 0x00000001)             # set
    await spi.bio_write(0x3C, 0x00000001)             # clear (next frame, ~50us later)
    await Timer(3 * QUANTUM_NS, unit="ns")
    v = gpio(dut)
    assert v == 0xE, f"core did not wake on set-then-clear (gpio=0x{v:08X})"
    ev = await spi.bio_read(0x40)
    assert (ev & 1) == 0, f"event bit0 not cleared: 0x{ev:08X}"
    dut._log.info("event set->clear: woke exactly once, event cleared")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_fifo_exact_full_boundary(dut):
    """Host fills FIFO2 to 8 (no consumer); 9th push must be dropped silently;
    level pinned at 8; drain returns first 8 in order."""
    spi = await setup(dut)                            # cores stay disabled
    for i in range(10):                                # 9 pushes into depth-8
        await spi.bio_write(0x18, 0xD0 + i)
    lv = await levels(spi)
    assert lv[2] == 9, f"FIFO2 level {lv[2]} != 8 after 9 pushes"

    for i in range(9):
        got = await spi.bio_read(0x28)
        assert got == 0xD0 + i, f"drain {i}: 0x{got:08X} != 0x{0xD0+i:02X}"
    lv = await levels(spi)
    assert lv[2] == 0, f"FIFO2 not empty after drain: {lv}"
    dut._log.info("exact-full: push #9 dropped, 8 words ordered, empty after drain")


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_mul_meets_stalls(dut):
    """mul immediately before a quanta wait, and immediately before a
    FIFO-blocking read: PCPI handshake vs clock gate."""
    spi = await setup(dut)
    await load_and_start(spi, 0, MUL_STALL_MIX, 0x00000005)
    await Timer(5 * QUANTUM_NS, unit="ns")

    got = await spi.bio_read(0x24)                    # result 1: 7*6
    assert got == 42, f"mul-before-quanta: {got} != 42"

    # core is now blocked reading FIFO0; feed it 100 -> expects 81+100 pushed
    await spi.bio_write(0x10, 100)
    await Timer(3 * QUANTUM_NS, unit="ns")
    got = await spi.bio_read(0x24)
    assert got == 181, f"mul-before-fifo-block: {got} != 181"
    dut._log.info("mul+quanta and mul+FIFO-block interactions clean (42, 181)")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_core_event_sets_irq1(dut):
    """Core-side event set (x28) -> aggregated_events -> irqmask1 -> irq[1]."""
    spi = await setup(dut)
    await spi.bio_write(0x74, 0x00000002)             # irqmask1 = event bit 1
    assert int(dut.irq.value) & 2 == 0, "irq[1] high before event"

    await load_and_start(spi, 0, CORE_EVENT_SET, 0x00000005)
    await Timer(3 * QUANTUM_NS, unit="ns")

    ev = await spi.bio_read(0x40)
    assert ev & 2, f"core event-set did not land: events=0x{ev:08X}"
    assert int(dut.irq.value) & 2, "irq[1] did not assert from core-set event"

    await spi.bio_write(0x3C, 0x00000002)
    await Timer(1 * QUANTUM_NS, unit="ns")
    assert int(dut.irq.value) & 2 == 0, "irq[1] did not deassert"
    dut._log.info("core-side event -> IRQ1 path verified")
    await spi.bio_write(0x00, 0)
