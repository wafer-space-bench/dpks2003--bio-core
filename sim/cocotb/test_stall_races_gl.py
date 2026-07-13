# test_stall_races_gl.py — chip_top level (GL netlist or RTL chip-top)
# Promoted from test_stall_races.py (RTL 6/6). GL-relevant subset:
#   1. push phase sweep         (wakeup via icgtp at varied clock alignment)
#   2. full-FIFO backpressure   (core WRITE-block through the clock gate)
#   3. mul meets stalls         (PCPI handshake vs gate, on real mult gates)
#   4. core-side event -> IRQ1  (second IRQ pad, core-side event writer)
# Not promoted: set-then-clear + exact-full boundary (logic-only, RTL suffices).
#
# Run: GL=1 POWER=0 NETLIST=<nl.v> PDK_ROOT=... \
#      TEST_MODULE=test_stall_races_gl python3 chip_top_tb.py

import cocotb
from cocotb.triggers import Timer, ClockCycles
from gl_common import start_up, read_gpio, pad_bit, IRQ1_PAD

CLK_NS     = 100                       # clk_PAD (10 MHz)
QDIV       = 100
QUANTUM_NS = QDIV * CLK_NS

FIFO_ECHO = [
    0xFFF00293, 0x00028C13, 0x00028D13,
    0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F,
]

PRODUCER = [
    0x10000313, 0x00030893, 0x00130313, 0xFF9FF06F,
]

MUL_STALL_MIX = [
    0x00700513, 0x00600593, 0x02B50633, 0x00060893,
    0x00000A13,
    0x00900513, 0x00900593, 0x02B50633,
    0x00080313, 0x006606B3, 0x00068893,
    0x0000006F,
]

CORE_EVENT_SET = [
    0x00200313, 0x00030E13, 0x0000006F,
]


async def setup(dut):
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x3C, 0x00FFFFFF)
    return spi


async def load_and_start(spi, core, prog, en_word):
    await spi.bio_write(0xE0, core)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x50 + 4 * core, QDIV << 16)
    await spi.bio_write(0x00, en_word)


async def levels(spi):
    v = await spi.bio_read(0x0C)
    return [(v >> (4 * i)) & 0xF for i in range(4)]


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_push_phase_sweep(dut):
    """Wakeup-from-gated at 8 clock phases; exactly-once echo each time."""
    spi = await setup(dut)
    await load_and_start(spi, 0, FIFO_ECHO, 0x00000005)
    await Timer(5 * QUANTUM_NS, unit="ns")

    for phase in range(8):
        await ClockCycles(dut.clk_PAD, phase * 13)
        val = 0xC0 + phase
        await spi.bio_write(0x10, val)
        await Timer(3 * QUANTUM_NS, unit="ns")
        v = read_gpio(dut)
        assert v == val, f"phase {phase}: echo 0x{v:08X} != 0x{val:02X}"
        lv = await levels(spi)
        assert lv[0] == 0, f"phase {phase}: FIFO0 level {lv[0]} != 0 after echo"
        dut._log.info(f"phase {phase}: 0x{val:02X} exactly once")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_full_fifo_backpressure(dut):
    """Producer write-blocks at capacity 9 (8 storage + 1 output stage);
    12 ordered pops under live backpressure."""
    spi = await setup(dut)
    await load_and_start(spi, 0, PRODUCER, 0x00000005)
    await Timer(20 * QUANTUM_NS, unit="ns")

    lv = await levels(spi)
    assert lv[1] == 9, f"FIFO1 full = 9 (8+1 output reg), levels={lv}"

    expected = 0x100
    for i in range(12):
        got = await spi.bio_read(0x24)                # SINGLE read: pops!
        assert got == expected, f"pop {i}: 0x{got:08X} != 0x{expected:08X}"
        expected += 1
        await Timer(2 * QUANTUM_NS, unit="ns")
        lv = await levels(spi)
        assert lv[1] <= 9, f"level exceeded 9: {lv}"
    dut._log.info("backpressure at GL: cap 9, 12 pops strictly ordered")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_mul_meets_stalls(dut):
    """mul->quanta-wait and mul->FIFO-block on the real multiplier gates."""
    spi = await setup(dut)
    await load_and_start(spi, 0, MUL_STALL_MIX, 0x00000005)
    await Timer(5 * QUANTUM_NS, unit="ns")

    got = await spi.bio_read(0x24)
    assert got == 42, f"mul-before-quanta: {got} != 42"

    await spi.bio_write(0x10, 100)
    await Timer(3 * QUANTUM_NS, unit="ns")
    got = await spi.bio_read(0x24)
    assert got == 181, f"mul-before-fifo-block: {got} != 181"
    dut._log.info("mul+stall interactions clean at GL (42, 181)")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_core_event_sets_irq1(dut):
    """Core-side event set (x28) -> aggregator -> irqmask1 -> IRQ1 PAD
    (bidir_PAD[2] — first-ever exercise of the second IRQ pad)."""
    spi = await setup(dut)
    await spi.bio_write(0x74, 0x00000002)             # irqmask1 = event bit 1
    b = pad_bit(dut, IRQ1_PAD)
    assert b == 0, f"IRQ1 pad not low before event (got {b!r})"

    await load_and_start(spi, 0, CORE_EVENT_SET, 0x00000005)
    await Timer(3 * QUANTUM_NS, unit="ns")

    ev = await spi.bio_read(0x40)
    assert ev & 2, f"core event-set did not land: 0x{ev:08X}"
    b = pad_bit(dut, IRQ1_PAD)
    assert b == 1, f"IRQ1 pad did not assert (got {b!r})"

    await spi.bio_write(0x3C, 0x00000002)
    await Timer(1 * QUANTUM_NS, unit="ns")
    b = pad_bit(dut, IRQ1_PAD)
    assert b == 0, f"IRQ1 pad did not deassert (got {b!r})"
    dut._log.info("core event -> IRQ1 pad verified at GL")
    await spi.bio_write(0x00, 0)
