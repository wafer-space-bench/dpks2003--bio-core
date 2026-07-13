# test_bootstrap_corners.py — chip_top level (RTL chip-top or GL netlist)
#
# Targets the rebuilt enable/restart/stall bootstrap machinery:
#   1. restart while quanta-stalled      (restart override vs live gated clock)
#   2. restart while FIFO-blocked        (same, deepest stall)
#   3. enable/disable churn x5           (a_restart window fired repeatedly)
#   4. garbage-IMEM neighbor isolation   (enabled-but-insane core 1 vs core 0)
#   5. MUL coverage                      (first-ever test of the fast multiplier)
#
# Run (GL):  GL=1 POWER=0 NETLIST=<nl.v> PDK_ROOT=... \
#            TEST_MODULE=test_bootstrap_corners python3 chip_top_tb.py
# Run (RTL chip-top): TEST_MODULE=test_bootstrap_corners python3 chip_top_tb.py

import cocotb
from cocotb.triggers import Timer
from gl_common import start_up, read_gpio, GPIO_BASE

CLK_NS     = 100                     # clk_PAD period (10 MHz)
QDIV       = 100
QUANTUM_NS = QDIV * CLK_NS           # 10_000 ns per quantum

WALKER = [
    0xFFF00293, 0x00028C13, 0x00100513,
    0x00050A93, 0x00151513, 0x00051463, 0x00100513,
    0x00000A13, 0xFEDFF06F, 0x00000013,
]

FIFO_ECHO = [
    0xFFF00293, 0x00028C13, 0x00028D13,
    0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F,
]

# MUL program: 0*5, -1*-1, 0xFFFF*0x10001 -> push each to FIFO1 (x17)
MUL_PROG = [
    0x00000513,  # li   a0, 0
    0x00500593,  # li   a1, 5
    0x02B50633,  # mul  a2, a0, a1
    0x00060893,  # mv   x17, a2   (FIFO1 push)
    0xFFF00513,  # li   a0, -1
    0xFFF00593,  # li   a1, -1
    0x02B50633,  # mul  a2, a0, a1
    0x00060893,  # mv   x17, a2
    0x00010537,  # lui  a0, 0x10
    0xFFF50513,  # addi a0, a0, -1    -> 0x0000FFFF
    0x000105B7,  # lui  a1, 0x10
    0x00158593,  # addi a1, a1, 1     -> 0x00010001
    0x02B50633,  # mul  a2, a0, a1
    0x00060893,  # mv   x17, a2
    0x0000006F,  # park
]
MUL_EXPECTED = [0x00000000, 0x00000001, 0xFFFFFFFF]


async def setup(dut):
    spi = await start_up(dut)                 # clk_PAD, reset, pad-level SPI
    await spi.bio_write(0x00, 0x00000000)     # cores disabled
    return spi


async def load(spi, core, prog):
    await spi.bio_write(0xE0, core)
    await spi.bio_imem_burst_write(0, prog)


async def levels(spi):
    v = await spi.bio_read_double(0x0C)
    return [(v >> (4 * i)) & 0xF for i in range(4)]


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_restart_while_quanta_stalled(dut):
    """Restart must ungate a core frozen in a quanta stall and relaunch it."""
    spi = await setup(dut)
    await load(spi, 0, WALKER)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)              # restart0|en0
    await Timer(6 * QUANTUM_NS, unit="ns")             # run several quanta

    v1 = read_gpio(dut)
    assert v1 != 0, f"walker not running before restart (gpio=0x{v1:08X})"
    dut._log.info(f"pre-restart gpio = 0x{v1:08X}")

    # restart WHILE running (walker spends ~95% of cycles quanta-gated)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(int(2.5 * QUANTUM_NS), unit="ns")
    v2 = read_gpio(dut)
    assert v2 in (0x1, 0x2, 0x4, 0x8), \
        f"walker did not relaunch from origin: 0x{v2:08X} (expected bit 0..3)"
    dut._log.info(f"post-restart gpio = 0x{v2:08X} — relaunched OK")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_restart_while_fifo_blocked(dut):
    """Restart must recover a core whose clock is gated on an empty-FIFO read."""
    spi = await setup(dut)
    await load(spi, 0, FIFO_ECHO)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(5 * QUANTUM_NS, unit="ns")             # now blocked on FIFO0

    await spi.bio_write(0x10, 0x000000AA)              # prove alive: echo once
    await Timer(3 * QUANTUM_NS, unit="ns")
    v = read_gpio(dut)
    assert v == 0xAA, f"pre-restart echo failed: 0x{v:08X}"

    await spi.bio_write(0x00, 0x00000005)              # restart from GATED state
    await Timer(3 * QUANTUM_NS, unit="ns")

    await spi.bio_write(0x10, 0x00000055)              # fresh echo after relaunch
    await Timer(3 * QUANTUM_NS, unit="ns")
    v = read_gpio(dut)
    assert v == 0x55, f"post-restart echo failed: 0x{v:08X} (expected 0x55)"
    dut._log.info("restart from FIFO-blocked (gated) state recovered cleanly")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_enable_disable_churn(dut):
    """5 rounds enable->run->disable->re-enable(+restart) on CORE 1: identical
    behavior each round; a_restart window fires repeatedly with prior state."""
    spi = await setup(dut)
    await load(spi, 1, FIFO_ECHO)
    await spi.bio_write(0x54, QDIV << 16)              # DIV1

    for rnd in range(5):
        await spi.bio_write(0x00, 0x0000000A)          # restart1|en1
        await Timer(4 * QUANTUM_NS, unit="ns")
        await spi.bio_write(0x10, 0x000000B0 + rnd)    # FIFO0 (what echo reads)
        await Timer(3 * QUANTUM_NS, unit="ns")
        v = read_gpio(dut)
        assert v == 0xB0 + rnd, f"round {rnd}: echo 0x{v:08X} != 0x{0xB0+rnd:02X}"
        await spi.bio_write(0x00, 0x00000000)          # disable
        await Timer(1 * QUANTUM_NS, unit="ns")
        dut._log.info(f"round {rnd}: OK")

    # documented corner: re-enable WITHOUT restart — must not corrupt (no X);
    # semantics = resume from stale state
    await spi.bio_write(0x00, 0x00000002)              # en1 only
    await Timer(4 * QUANTUM_NS, unit="ns")
    raw = str(dut.bidir_PAD.value)
    assert "x" not in raw.lower()[9:41], f"X on pads after no-restart resume: {raw}"
    dut._log.info(f"no-restart resume: pads defined ({raw})")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_garbage_imem_isolation(dut):
    """Enable BOTH cores, core 1's IMEM never loaded (garbage). Core 0 must
    stay perfect throughout; pads must never go X."""
    spi = await setup(dut)
    await load(spi, 0, FIFO_ECHO)                      # core 0 only!
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x54, QDIV << 16)
    await spi.bio_write(0x00, 0x0000000F)              # BOTH: restart+en

    for i, val in enumerate([0x11, 0x22, 0x33]):
        await spi.bio_write(0x10, val)
        await Timer(3 * QUANTUM_NS, unit="ns")
        raw = str(dut.bidir_PAD.value)
        assert "x" not in raw.lower()[9:41], \
            f"pads X with garbage core 1 enabled (push {i}): {raw}"
        v = read_gpio(dut)
        assert (v & 0xFF) == val, \
            f"core 0 corrupted by garbage core 1: 0x{v:08X} != 0x{val:02X}"
    dut._log.info("core 0 unaffected by enabled-garbage core 1 — isolation holds")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_mul_coverage(dut):
    """First exercise of ENABLE_FAST_MUL: exact results via FIFO1 readback."""
    spi = await setup(dut)
    await load(spi, 0, MUL_PROG)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(10 * QUANTUM_NS, unit="ns")            # program parks quickly

    lv = await levels(spi)
    assert lv[1] == 3, f"expected 3 results in FIFO1, levels={lv}"

    for i, exp in enumerate(MUL_EXPECTED):
        got = await spi.bio_read(0x24) 
        assert got == exp, f"mul result {i}: 0x{got:08X} != 0x{exp:08X}"
        dut._log.info(f"  mul[{i}] = 0x{got:08X} OK")
    dut._log.info("fast multiplier verified: 0*5, -1*-1, 0xFFFF*0x10001")
    await spi.bio_write(0x00, 0)
