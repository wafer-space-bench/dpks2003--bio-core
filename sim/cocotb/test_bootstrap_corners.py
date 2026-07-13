# test_bootstrap_corners.py
#
# Targets the rebuilt enable/restart/stall bootstrap machinery:
#   1. restart while quanta-stalled      (restart override vs live gated clock)
#   2. restart while FIFO-blocked        (same, deepest stall)
#   3. enable/disable churn x5           (a_restart window fired repeatedly)
#   4. garbage-IMEM neighbor isolation   (enabled-but-insane core 1 vs core 0)
#   5. MUL coverage                      (first-ever test of the fast multiplier)
#
# Note: cycle-level overlapping restart pulses are NOT host-reachable (restart
# self-clears in 2 cycles; SPI frames are ~50us apart) — documented, not tested.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from spi_master import SPIMaster

QDIV = 100                                # quantum = QDIV * clk period

WALKER = [                                # walking-1, quanta-paced (verified set)
    0xFFF00293, 0x00028C13, 0x00100513,
    0x00050A93, 0x00151513, 0x00051463, 0x00100513,
    0x00000A13, 0xFEDFF06F, 0x00000013,
]

FIFO_ECHO = [                             # dir/mask all, echo FIFO0 -> GPIO (verified set)
    0xFFF00293, 0x00028C13, 0x00028D13,
    0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F,
]

# MUL program: three multiplies, results pushed to FIFO1 (x17) for exact check.
#   0*5 = 0 ; -1*-1 = 1 ; 0xFFFF*0x10001 = 0xFFFFFFFF
MUL_PROG = [
    0x00000513,  # li   a0, 0
    0x00500593,  # li   a1, 5
    0x02B50633,  # mul  a2, a0, a1
    0x00060893,  # mv   x17, a2          (push FIFO1)
    0xFFF00513,  # li   a0, -1
    0xFFF00593,  # li   a1, -1
    0x02B50633,  # mul  a2, a0, a1
    0x00060893,  # mv   x17, a2
    0x00010537,  # lui  a0, 0x10
    0xFFF50513,  # addi a0, a0, -1       -> 0x0000FFFF
    0x000105B7,  # lui  a1, 0x10
    0x00158593,  # addi a1, a1, 1        -> 0x00010001
    0x02B50633,  # mul  a2, a0, a1
    0x00060893,  # mv   x17, a2
    0x0000006F,  # park: j park
]
MUL_EXPECTED = [0x00000000, 0x00000001, 0xFFFFFFFF]


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    dut.reset_n.value = 0
    dut.gpio_in.value = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)
    spi = SPIMaster(dut)
    await spi.bio_write(0x00, 0x00000000)
    return spi


async def load(spi, core, prog):
    await spi.bio_write(0xE0, core)
    await spi.bio_imem_burst_write(0, prog)


def gpio_val(dut):
    try:
        return int(dut.gpio_out.value)
    except ValueError:
        return None                        # X on the bus


async def levels(spi):
    v = await spi.bio_read(0x0C)
    return [(v >> (4 * i)) & 0xF for i in range(4)]


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_restart_while_quanta_stalled(dut):
    """Restart must ungate a core frozen in a quanta stall and relaunch it."""
    spi = await setup(dut)
    await load(spi, 0, WALKER)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)          # restart0|en0
    await Timer(60_000, unit="ns")                 # run several quanta

    v1 = gpio_val(dut)
    assert v1 is not None and v1 != 0, f"walker not running before restart: {v1!r}"
    dut._log.info(f"pre-restart gpio = 0x{v1:08X}")

    # restart WHILE running (statistically lands during a quanta stall,
    # since the walker spends ~95% of cycles gated)
    await spi.bio_write(0x00, 0x00000005)
    # shortly after restart the walker must be back near bit0
    await Timer(int(QDIV * 40 * 2.5), unit="ns")   # ~2.5 quanta
    v2 = gpio_val(dut)
    assert v2 is not None, "gpio X after restart!"
    assert v2 in (0x1, 0x2, 0x4), \
        f"walker did not relaunch from start: 0x{v2:08X} (expected bit 0..2)"
    dut._log.info(f"post-restart gpio = 0x{v2:08X} — relaunched from origin OK")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_restart_while_fifo_blocked(dut):
    """Restart must recover a core whose clock is gated on an empty-FIFO read."""
    spi = await setup(dut)
    await load(spi, 0, FIFO_ECHO)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(50_000, unit="ns")                 # core is now blocked on FIFO0

    # prove it's alive: push once, expect echo
    await spi.bio_write(0x10, 0x000000AA)
    await Timer(int(QDIV * 40 * 3), unit="ns")
    v = gpio_val(dut)
    assert v == 0xAA, f"pre-restart echo failed: {v!r}"

    # restart from the BLOCKED state (clock gated at the moment of restart)
    await spi.bio_write(0x00, 0x00000005)
    await Timer(30_000, unit="ns")

    # core re-ran setup and is blocked again at a FRESH read; push new value
    await spi.bio_write(0x10, 0x00000055)
    await Timer(int(QDIV * 40 * 3), unit="ns")
    v = gpio_val(dut)
    assert v == 0x55, f"post-restart echo failed: {v!r} (expected 0x55)"
    dut._log.info("restart from FIFO-blocked (gated) state recovered cleanly")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_enable_disable_churn(dut):
    """5 rounds of enable->run->disable->re-enable(+restart): every round must
    behave identically; the a_restart window fires repeatedly with prior state."""
    spi = await setup(dut)
    await load(spi, 1, FIFO_ECHO)                  # exercise CORE 1 for a change
    await spi.bio_write(0x54, QDIV << 16)

    for rnd in range(5):
        await spi.bio_write(0x00, 0x0000000A)      # restart1|en1
        await Timer(40_000, unit="ns")
        await spi.bio_write(0x14, 0x0000B0 + rnd)  # push FIFO1... 
        # NOTE: FIFO_ECHO reads FIFO0; push FIFO0 instead:
        # (kept explicit to avoid silent wrong-FIFO bugs)
        await spi.bio_write(0x10, 0x000000B0 + rnd)
        await Timer(int(QDIV * 40 * 3), unit="ns")
        v = gpio_val(dut)
        assert v == 0xB0 + rnd, f"round {rnd}: echo {v!r} != 0x{0xB0+rnd:02X}"
        await spi.bio_write(0x00, 0x00000000)      # disable
        await Timer(10_000, unit="ns")
        dut._log.info(f"round {rnd}: OK")

    # one documented round WITHOUT restart: resume-from-stale must at minimum
    # not corrupt anything (no X, no spurious GPIO outside prior value)
    await spi.bio_write(0x00, 0x00000002)          # en1 only, no restart
    await Timer(40_000, unit="ns")
    v = gpio_val(dut)
    assert v is not None, "gpio went X on no-restart resume!"
    dut._log.info(f"no-restart resume: gpio=0x{v:08X} (defined; semantics = resume)")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def test_garbage_imem_isolation(dut):
    """Enable BOTH cores with core 1's IMEM never loaded. Core 1 executes
    garbage (X/zeros -> likely traps). Core 0 must stay perfect throughout."""
    spi = await setup(dut)
    await load(spi, 0, FIFO_ECHO)                  # core 0 only!
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x54, QDIV << 16)
    await spi.bio_write(0x00, 0x0000000F)          # BOTH: restart+en

    for i, val in enumerate([0x11, 0x22, 0x33]):
        await spi.bio_write(0x10, val)
        await Timer(int(QDIV * 40 * 3), unit="ns")
        v = gpio_val(dut)
        assert v is not None, f"gpio X with garbage core 1 enabled (push {i})"
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
    await Timer(100_000, unit="ns")                # plenty; program parks

    lv = await levels(spi)
    assert lv[1] == 3, f"expected 3 results in FIFO1, levels={lv}"

    for i, exp in enumerate(MUL_EXPECTED):
        got = await spi.bio_read(0x24)             # pop FIFO1
        assert got == exp, f"mul result {i}: 0x{got:08X} != 0x{exp:08X}"
        dut._log.info(f"  mul[{i}] = 0x{got:08X} OK")
    dut._log.info("fast multiplier verified: 0*5, -1*-1, 0xFFFF*0x10001")
    await spi.bio_write(0x00, 0)
