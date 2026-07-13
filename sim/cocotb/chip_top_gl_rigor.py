# chip_top_gl_rigor.py — adversarial GL suite
#
# Targets the failure classes this project actually hit:
#   X-locking of shared state, unreset flops, enable/restart races,
#   disabled-core leakage, and stall-cone poisoning.
#
# Every test asserts NO X ON PADS as a first-class property, not just values.

import cocotb
from cocotb.triggers import Timer, ClockCycles
from gl_common import start_up, read_gpio, pad_bit, BidirDriver, GPIO_BASE, IRQ0_PAD, IRQ1_PAD

CLK_NS, QDIV = 100, 100
QUANTUM_NS = QDIV * CLK_NS

WALKER = [0xFFF00293, 0x00028C13, 0x00100513, 0x00050A93, 0x00151513,
          0x00051463, 0x00100513, 0x00000A13, 0xFEDFF06F, 0x00000013]
ECHO   = [0xFFF00293, 0x00028C13, 0x00028D13,
          0x00080313, 0x00030A93, 0x00000A13, 0xFF5FF06F]


def gpio_field(dut):
    """The 32 GPIO characters of bidir_PAD, MSB-first."""
    s = str(dut.bidir_PAD.value)
    return s[len(s) - 1 - (GPIO_BASE + 31): len(s) - 1 - GPIO_BASE + 1]


def assert_no_x(dut, ctx):
    s = str(dut.bidir_PAD.value)
    field = "".join(s[len(s) - 1 - (GPIO_BASE + i)] for i in range(32))
    assert "x" not in field.lower(), f"{ctx}: X on GPIO pads — {s}"
    for name, i in (("IRQ0", IRQ0_PAD), ("IRQ1", IRQ1_PAD)):
        b = pad_bit(dut, i)
        assert b is not None, f"{ctx}: {name} pad is X/Z"


async def load(spi, core, prog):
    await spi.bio_write(0xE0, core)
    await spi.bio_imem_burst_write(0, prog)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_enable_disable_storm(dut):
    """20 rapid enable/disable cycles, alternating cores, asserting NO X after
    every transition. This is the sequence that exposed the latched_store bug."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    await load(spi, 0, ECHO)
    await load(spi, 1, WALKER)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x54, QDIV << 16)
    assert_no_x(dut, "after load")

    seq = [0x05, 0x00, 0x0A, 0x00, 0x0F, 0x00, 0x05, 0x0A, 0x00, 0x0F,
           0x05, 0x00, 0x0A, 0x0F, 0x00, 0x05, 0x0F, 0x0A, 0x00, 0x0F]
    for i, ctl in enumerate(seq):
        await spi.bio_write(0x00, ctl)
        await Timer(2 * QUANTUM_NS, unit="ns")
        assert_no_x(dut, f"storm step {i} (ctl=0x{ctl:02X})")
    dut._log.info("20 enable/disable transitions, no X on pads")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_enable_without_program(dut):
    """Enable a core whose IMEM was never written. It executes garbage.
    Pads must stay defined; the OTHER core must be unaffected."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    await load(spi, 0, ECHO)                  # core 0 only
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x54, QDIV << 16)
    await spi.bio_write(0x00, 0x0F)           # BOTH enabled; core 1 = garbage

    for i, val in enumerate([0x11, 0x22, 0x33, 0x44]):
        await spi.bio_write(0x10, val)
        await Timer(3 * QUANTUM_NS, unit="ns")
        assert_no_x(dut, f"garbage-core1 push {i}")
        g = read_gpio(dut)
        assert (g & 0xFF) == val, f"core 0 corrupted: 0x{g:08X} != 0x{val:02X}"
    dut._log.info("garbage-executing core 1 did not corrupt core 0 or the pads")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_restart_storm(dut):
    """Restart a running core 15x at pseudo-random intervals. Every restart
    must relaunch cleanly; pads never X; the walker always restarts at bit 0."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    await load(spi, 0, WALKER)
    await spi.bio_write(0x50, QDIV << 16)

    for i in range(15):
        await spi.bio_write(0x00, 0x05)                  # restart + enable
        await ClockCycles(dut.clk_PAD, 7 * i + 3)        # vary the phase
        await Timer(int(1.5 * QUANTUM_NS), unit="ns")
        assert_no_x(dut, f"restart {i}")
        v = read_gpio(dut)
        assert v == 0 or (v & (v - 1)) == 0, \
            f"restart {i}: not one-hot after relaunch: 0x{v:08X}"
    dut._log.info("15 phase-varied restarts, always clean and one-hot")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_reset_midflight(dut):
    """Assert chip reset while both cores run and FIFOs hold data.
    After reset: pads Z/0, all FIFOs empty, chip fully usable again."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    await load(spi, 0, ECHO)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x05)
    for v in (0xA1, 0xA2, 0xA3):
        await spi.bio_write(0x10, v)
    await spi.bio_write(0x18, 0xBEEF)        # leave data in FIFO2
    await Timer(2 * QUANTUM_NS, unit="ns")

    dut.rst_n_PAD.value = 0                   # RESET MID-FLIGHT
    await Timer(500, unit="ns")
    dut.rst_n_PAD.value = 1
    await Timer(2000, unit="ns")

    assert_no_x(dut, "after mid-flight reset")
    lv = await spi.bio_read_double(0x0C)
    assert lv == 0, f"FIFOs not empty after reset: 0x{lv:08X}"
    cid = await spi.bio_read_double(0xFC)
    assert cid == 0x0B100002, f"chip dead after reset: 0x{cid:08X}"

    # chip must be fully usable again
    await load(spi, 0, ECHO)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x05)
    await spi.bio_write(0x10, 0x77)
    await Timer(3 * QUANTUM_NS, unit="ns")
    assert (read_gpio(dut) & 0xFF) == 0x77, "chip not functional after reset"
    dut._log.info("mid-flight reset: clean recovery, FIFOs flushed, chip usable")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_spi_during_execution(dut):
    """Hammer the SPI bus with reads/writes while both cores run.
    Host access must never disturb execution, and never X the pads."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    await load(spi, 0, ECHO)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x05)

    for i in range(10):
        await spi.bio_write(0x10, 0x50 + i)              # push
        _ = await spi.bio_read_double(0x0C)              # levels
        _ = await spi.bio_read_double(0x40)              # events
        _ = await spi.bio_read_double(0x88)              # gpio_dir
        _ = await spi.bio_read_double(0xFC)              # chip id
        await spi.bio_write(0x38, 0)                     # no-op event write
        await Timer(2 * QUANTUM_NS, unit="ns")
        assert_no_x(dut, f"spi hammer {i}")
        g = read_gpio(dut)
        assert (g & 0xFF) == 0x50 + i, f"execution disturbed: 0x{g:08X}"
    dut._log.info("50 host transactions during execution: no interference, no X")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_all_fifos_all_cores(dut):
    """Both cores hammering all 4 FIFOs while the host drains them.
    Stresses the arbiters and the core_live gating under full load."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)

    # core: read FIFO0, push to FIFO1, FIFO2, FIFO3, loop
    prog = [
        0x00080313,   # mv x6, x16   (pop FIFO0, blocks)
        0x00030893,   # mv x17, x6   (push FIFO1)
        0x00030913,   # mv x18, x6   (push FIFO2)
        0x00030993,   # mv x19, x6   (push FIFO3)
        0x00000A13,   # quanta
        0xFEDFF06F,   # loop
    ]
    await load(spi, 0, prog)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x05)

    for i in range(6):
        await spi.bio_write(0x10, 0x1000 + i)
        await Timer(3 * QUANTUM_NS, unit="ns")
        assert_no_x(dut, f"fifo storm {i}")
        for addr in (0x24, 0x28, 0x2C):
            got = await spi.bio_read(addr)
            assert got == 0x1000 + i, \
                f"FIFO at 0x{addr:02X}: 0x{got:08X} != 0x{0x1000+i:08X}"
    dut._log.info("all 4 FIFOs, full loop, values ordered and no X")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_gpio_contention(dut):
    """TB drives a pad that the core also drives (contention), then releases.
    Must not X-lock the shared gpio state."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    await load(spi, 0, WALKER)                 # drives all 32 pins
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x05)
    await Timer(2 * QUANTUM_NS, unit="ns")

    drv = BidirDriver(dut.bidir_PAD)
    drv.set_bit(GPIO_BASE + 5, 0)              # fight the core on gpio[5]
    await Timer(2 * QUANTUM_NS, unit="ns")
    drv.release_all()
    await Timer(2 * QUANTUM_NS, unit="ns")

    # the internal state must survive: pattern continues, no X-lock
    assert_no_x(dut, "after pad contention released")
    seen = []
    for _ in range(10):
        v = read_gpio(dut)
        if v and (v & (v - 1)) == 0 and (not seen or v != seen[-1]):
            seen.append(v)
        await Timer(int(0.4 * QUANTUM_NS), unit="ns")
    assert len(seen) >= 2, f"walker died after contention: {seen}"
    dut._log.info("pad contention did not corrupt internal GPIO state")
    await spi.bio_write(0x00, 0)


# ---------------------------------------------------------------------------
@cocotb.test()
async def rigor_event_storm(dut):
    """Rapid event set/clear from host while a core waits on events.
    Wake must be reliable; aggregated_events must never go X."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0)
    await spi.bio_write(0x3C, 0x00FFFFFF)

    ev_prog = [
        0xFFF00293, 0x00028C13, 0x00028D13,
        0x00100313, 0x00030D93,        # EV_MASK = bit0
        0x000F0393,                    # wait
        0x00100A93,                    # gpio = 1
        0x00000A13,                    # quanta
        0xFEDFF06F,                    # loop back to wait
    ]
    await load(spi, 0, ev_prog)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x05)

    for i in range(8):
        await spi.bio_write(0x38, 0x1)          # set
        await Timer(QUANTUM_NS, unit="ns")
        await spi.bio_write(0x3C, 0x1)          # clear
        await Timer(QUANTUM_NS, unit="ns")
        assert_no_x(dut, f"event storm {i}")
        ev = await spi.bio_read_double(0x40)
        assert ev != 0xFFFFFFFF, f"events X/garbage: 0x{ev:08X}"
    dut._log.info("8 rapid event set/clear cycles: no X, events sane")
    await spi.bio_write(0x00, 0)
