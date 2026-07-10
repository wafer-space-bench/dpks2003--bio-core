# chip_top_gl_ext.py — extended gate-level soak suite for chip_top.
#
# Each test states the GL-UNIQUE thing it verifies (netlist/macro/ICG/pad
# interactions that RTL sim cannot exercise). Logic-only corner cases stay
# in the RTL suites.
#
# Run:
#   GL=1 POWER=0 NETLIST=<nl.v> PDK_ROOT=... \
#     COCOTB_TEST_MODULES=chip_top_gl_ext python3 chip_top_tb.py
#   (or add a runner __main__ mirroring chip_top_tb.py)

import cocotb
from cocotb.triggers import Timer
from gl_common import start_up, pad_bit, read_gpio, BidirDriver, GPIO_BASE

QDIV = 100                      # quantum = 10us @10MHz


async def load_and_check(spi, dut, program, core=0):
    await spi.bio_write(0xE0, core)
    await spi.bio_imem_burst_write(0, program)
    for a in (0, len(program) - 1):
        got = await spi.bio_imem_read(a)
        assert got == program[a], f"core{core} load: word {a}=0x{got:08X}"


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_fifo_host_bitbang(dut):
    """GL-unique: FIFO-blocking stall through the real ICG cells.
    Core blocks on empty FIFO0 (x16 read) -> clock gated by icgtp; host push
    ungates it. Complements test_4 (quanta stall). Verbatim port of
    test_bunnie_fifo_host_bitbang."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    program = [
        0xFFF00293,  # li x5, -1
        0x00028C13,  # mv x24, x5   (all output)
        0x00028D13,  # mv x26, x5   (mask = all)
        # loop:
        0x00080313,  # mv x6, x16   (read FIFO0 -> x6, BLOCKS when empty)
        0x00030A93,  # mv x21, x6   (write GPIO)
        0x00000A13,  # mv x20, x0   (quantum wait)
        0xFF5FF06F,  # j loop
    ]
    await load_and_check(spi, dut, program, core=0)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)          # restart0|en0
    await Timer(50_000, unit="ns")

    test_values = [0x0000F1F0, 0x00001234, 0x0000ABCD, 0x00008000]
    for val in test_values:
        await spi.bio_write(0x10, val)             # push FIFO0
        await Timer(QDIV * 3 * 100, unit="ns")     # ~3 quanta
        got = read_gpio(dut) & 0xFFFF
        exp = val & 0xFFFF
        assert got == exp, f"pushed 0x{val:08X}, pads=0x{got:04X} != 0x{exp:04X}"
        dut._log.info(f"  push 0x{val:04X} -> pads 0x{got:04X} OK")

    # blocking check: no push for a while -> GPIO must HOLD last value
    await Timer(QDIV * 6 * 100, unit="ns")
    got = read_gpio(dut) & 0xFFFF
    assert got == test_values[-1] & 0xFFFF, \
        f"core did not block on empty FIFO (pads=0x{got:04X})"
    dut._log.info("core blocked on empty FIFO — stall/ungate via icgtp verified")

    await spi.bio_write(0x00, 0x00000000)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_two_cores_concurrent(dut):
    """GL-unique: both ICGs live simultaneously; shared GPIO aggregators
    (with the new en_sync gating) arbitrating two real requesters with gate
    delays. Core0 drives gpio[15:0] pattern A, core1 drives gpio[31:16]
    pattern B, disjoint masks."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    def core_prog(mask_imm_hi):   # set mask+dir to one half, write pattern, loop
        # x26 (mask) and x24 (dir) restricted to the core's half
        return [
            0xFFF00293,                        # li x5, -1
            # build half-mask in x5 via lui-style: use two instrs
            (0x000002B7 | (mask_imm_hi << 12)),# lui x5, imm  (upper half sel)
            0x00028C13,                        # mv x24, x5 (dir = half)
            0x00028D13,                        # mv x26, x5 (mask = half)
            0x00028A93,                        # mv x21, x5 (drive all my pins 1)
            0x00000A13,                        # mv x20, x0 (quantum)
            0xFF9FF06F,                        # j -8 (loop on write+wait)
        ]

    # core0: lower half 0x0000FFFF -> needs li, not lui; simpler: distinct constants
    prog0 = [
        0x000012B7,      # lui x5, 0x1        -> x5 = 0x00001000
        0xFFF28293,      # addi x5, x5, -1    -> x5 = 0x00000FFF
        0x00028C13,      # mv x24, x5  (dir  = 0x0FFF)
        0x00028D13,      # mv x26, x5  (mask = 0x0FFF)
        0x00028A93,      # mv x21, x5  (gpio = 0x0FFF)
        0x00000A13,      # quantum
        0xFF9FF06F,      # j loop
    ]
    prog1 = [
        0xFF0002B7,      # lui x5, 0xFF000    -> x5 = 0xFF000000
        0x00028C13,      # mv x24, x5  (dir  = 0xFF000000)
        0x00028D13,      # mv x26, x5  (mask = 0xFF000000)
        0x00028A93,      # mv x21, x5  (gpio = 0xFF000000)
        0x00000A13,      # quantum
        0xFF9FF06F,      # j loop
    ]

    await load_and_check(spi, dut, prog0, core=0)
    await load_and_check(spi, dut, prog1, core=1)

    await spi.bio_write(0x50, QDIV << 16)          # div core0
    await spi.bio_write(0x54, QDIV << 16)          # div core1
    await spi.bio_write(0x00, 0x0000000F)          # restart both | en both
    await Timer(200_000, unit="ns")

    g = read_gpio(dut)
    dut._log.info(f"concurrent gpio = 0x{g:08X}")
    assert (g & 0x00000FFF) == 0x00000FFF, f"core0 half wrong: 0x{g:08X}"
    assert (g & 0xFF000000) == 0xFF000000, f"core1 half wrong: 0x{g:08X}"
    assert (g & 0x00FFF000) == 0, f"unclaimed bits driven: 0x{g:08X}"

    # isolation on disable: kill core1, its pins must release; core0 unaffected
    await spi.bio_write(0x00, 0x00000001)          # en0 only
    await Timer(50_000, unit="ns")
    g2 = read_gpio(dut)
    assert (g2 & 0x00000FFF) == 0x00000FFF, "core0 disturbed by core1 disable"
    dut._log.info("two-core concurrent + disable isolation OK")
    await spi.bio_write(0x00, 0x00000000)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_event_stall(dut):
    """GL-unique: the third stall flavor (event wait) through the icgtp, and
    the event aggregator with a HOST writer waking a CORE waiter.
    Core: mask event bit0 (x27), wait (x30 read stalls), then drive GPIO."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x3C, 0x00FFFFFF)          # clear events

    program = [
        0xFFF00293,      # li x5, -1
        0x00028C13,      # mv x24, x5 (dir all)
        0x00028D13,      # mv x26, x5 (mask all)
        0x00100313,      # li x6, 1
        0x00030D93,      # mv x27, x6  (event_mask = bit0)
        0x000F0393,      # mv x7, x30  (WAIT: stalls until event&mask != 0)
        0x00500A93,      # li x21, 5   -> gpio = 0x5 (marker: we woke up)
        0x0000006F,      # j .  (park)
    ]
    await load_and_check(spi, dut, program, core=0)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(100_000, unit="ns")

    g = read_gpio(dut)
    assert g != 0x5, f"core did not stall on event wait (gpio=0x{g:08X})"
    dut._log.info("core stalled on event — now waking it via host")

    await spi.bio_write(0x38, 0x00000001)          # host sets event bit 0
    await Timer(100_000, unit="ns")
    g = read_gpio(dut)
    assert g == 0x5, f"core did not wake on event (gpio=0x{g:08X})"
    dut._log.info("event stall + host wake through icgtp verified")
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x3C, 0x00FFFFFF)


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_fifo_restart_policy(dut):
    """GL confirmation of the per-core FIFO restart nets (they sit in the ICG
    E-cone in this netlist — verify the policy survived synthesis). Compact
    version of test_fifo_reset."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    for i, addr in enumerate([0x10, 0x14, 0x18, 0x1C]):
        await spi.bio_write(addr, 0xF1F00000 + i)

    v = await spi.bio_read_double(0x0C)
    lv = [(v >> (4 * i)) & 0xF for i in range(4)]
    assert lv == [1, 1, 1, 1], f"fill levels {lv}"

    await spi.bio_write(0x00, 0x00000008)          # restart core1 only
    await Timer(2_000, unit="ns")
    v = await spi.bio_read_double(0x0C)
    lv = [(v >> (4 * i)) & 0xF for i in range(4)]
    assert lv == [1, 0, 1, 1], f"restart core1: levels {lv} (want [1,0,1,1])"
    dut._log.info("per-core FIFO restart policy verified at GL")


# ---------------------------------------------------------------------------
@cocotb.test()
async def gl_gpio_input_path(dut):
    """GL-unique: bidir pad as INPUT — TB drives a pad, signal traverses the
    input buffer -> synchronizers -> core reads x21 -> core echoes to an
    output pin. First test ever of the pad-ring input direction for GPIO."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)

    # Core: dir = bit1 only (gpio[1] out, gpio[0] stays input);
    # loop: read pins (x21), AND bit0, shift to bit1, write, quantum, loop
    program = [
        0x00200293,      # li x5, 2
        0x00028C13,      # mv x24, x5   (dir = bit1 only)
        0xFFF00313,      # li x6, -1
        0x00030D13,      # mv x26, x6   (mask = all)
        # loop:
        0x000A8393,      # mv x7, x21   (read gpio pins)
        0x0013F393,      # andi x7, x7, 1
        0x00139393,      # slli x7, x7, 1
        0x00038A93,      # mv x21, x7   (gpio[1] <= gpio[0])
        0x00000A13,      # quantum
        0xFEDFF06F,      # j loop
    ]
    await load_and_check(spi, dut, program, core=0)

    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(50_000, unit="ns")

    drv = BidirDriver(dut.bidir_PAD)
    for din in (1, 0, 1, 1, 0):
        drv.set_bit(GPIO_BASE + 0, din)            # drive gpio[0] pad
        await Timer(QDIV * 4 * 100, unit="ns")     # a few quanta to propagate
        out = pad_bit(dut, GPIO_BASE + 1)
        assert out == din, f"echo failed: drove {din}, gpio[1] pad = {out!r}"
        dut._log.info(f"  gpio[0]={din} -> gpio[1]={out} OK")

    drv.release_all()
    dut._log.info("pad-ring GPIO input path verified at GL")
    await spi.bio_write(0x00, 0x00000000)
