# test_gpio_invert.py — GPIO inversion boundary XORs (bio_lite_top level)
#
# Register map:
#   0x64 oe_invert  — XORs gpio_dir after the snap register
#   0x68 out_invert — XORs gpio_out after the snap register
#   0x6C in_invert  — XORs gpio_in before the synchronizers
#
# Readback semantics: 0x84 (gpio_out) and 0x88 (gpio_dir) return the PIN VIEW,
# i.e. post-inversion, matching bio_bdma.
#
# FAIL-BEFORE: on RTL without the XORs wired, all three tests must fail.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from spi_master import SPIMaster

CLK_NS, QDIV = 40, 100
QUANTUM_NS = QDIV * CLK_NS

# read gpio pins (x21) -> push to FIFO1 (x17), every quantum
IN_ECHO = [
    0x000A8313,   # mv x6, x21     (read pins)
    0x00030893,   # mv x17, x6     (push FIFO1)
    0x00000A13,   # mv x20, x0     (quantum)
    0xFF5FF06F,   # j loop
]

# dir/mask = all; drive gpio = 0x0000F0F0; park
OUT_DRIVE = [
    0xFFF00293,   # li   t0, -1
    0x00028C13,   # mv   x24, t0   (dir = all outputs)
    0x00028D13,   # mv   x26, t0   (mask = all)
    0x0000F5B7,   # lui  a1, 0xF          -> 0x0000F000
    0x0F058593,   # addi a1, a1, 0xF0     -> 0x0000F0F0
    0x00058A93,   # mv   x21, a1   (drive GPIO)
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
    await spi.bio_write(0x00, 0)
    return spi


async def load_start(spi, prog):
    await spi.bio_write(0xE0, 0)
    await spi.bio_imem_burst_write(0, prog)
    await spi.bio_write(0x50, QDIV << 16)
    await spi.bio_write(0x00, 0x05)          # restart0 | en0



# read pins (x21), write them to GPIO out (x21), loop
# gpio_out then mirrors gpio_pins directly — no FIFO, no pipeline.
IN_MIRROR = [
    0xFFF00293,   # li   t0, -1
    0x00028C13,   # mv   x24, t0     (dir = all outputs)
    0x00028D13,   # mv   x26, t0     (mask = all)
    0x000A8313,   # mv   x6, x21     (read pins)
    0x00030A93,   # mv   x21, x6     (write pins)
    0x00000A13,   # mv   x20, x0     (quantum)
    0xFEDFF06F,   # j loop (back to the read)
]


@cocotb.test()
async def test_in_invert(dut):
    """0x6C flips what the core reads from the pins."""
    spi = await setup(dut)

    # NOTE: dir=all-out means the core drives the pins, so drive gpio_in from
    # the TB and read the core's *echo* on gpio_out. out_invert stays 0.
    dut.gpio_in.value = 0x0000AAAA
    await load_start(spi, IN_MIRROR)
    await Timer(6 * QUANTUM_NS, unit="ns")

    got = int(dut.gpio_out.value)
    assert got == 0x0000AAAA, f"baseline echo: 0x{got:08X} != 0x0000AAAA"
    dut._log.info(f"in_invert=0: core echoes 0x{got:08X}")

    await spi.bio_write(0x6C, 0xFFFFFFFF)
    await Timer(6 * QUANTUM_NS, unit="ns")

    got = int(dut.gpio_out.value)
    assert got == 0xFFFF5555, f"inverted echo: 0x{got:08X} != 0xFFFF5555"
    dut._log.info(f"in_invert=~0: core echoes 0x{got:08X} — inversion verified")
    await spi.bio_write(0x00, 0)
#
#### -----------------------------------------------------------------------
@cocotb.test()
async def test_out_invert(dut):
    """0x68 flips what the pins show vs what the core wrote (XOR after snap)."""
    spi = await setup(dut)
    await load_start(spi, OUT_DRIVE)
    await Timer(3 * QUANTUM_NS, unit="ns")

    assert int(dut.gpio_out.value) == 0x0000F0F0, \
        f"baseline drive: 0x{int(dut.gpio_out.value):08X}"
    rb = await spi.bio_read(0x84)
    assert rb == 0x0000F0F0, f"0x84 baseline: 0x{rb:08X}"

    await spi.bio_write(0x68, 0x000000FF)     # invert low byte only
    await ClockCycles(dut.clk, 5)

    got = int(dut.gpio_out.value)
    assert got == 0x0000F00F, f"out_invert: pins 0x{got:08X} != 0x0000F00F"
    rb = await spi.bio_read(0x84)
    assert rb == 0x0000F00F, f"0x84 must read PIN VIEW (post-invert): 0x{rb:08X}"
    dut._log.info(f"out_invert=0xFF: core wrote 0xF0F0, pins show 0x{got:08X} OK")
    await spi.bio_write(0x00, 0)


## ---------------------------------------------------------------------------
@cocotb.test()
async def test_oe_invert(dut):
    """0x64 flips pin directions; 0x88 reads the pin view."""
    spi = await setup(dut)
    await load_start(spi, OUT_DRIVE)          # dir = 0xFFFFFFFF
    await Timer(3 * QUANTUM_NS, unit="ns")

    assert int(dut.gpio_dir.value) == 0xFFFFFFFF, "baseline dir"
    rb = await spi.bio_read(0x88)
    assert rb == 0xFFFFFFFF, f"0x88 baseline: 0x{rb:08X}"

    await spi.bio_write(0x64, 0x0000000F)     # flip low nibble to input
    await ClockCycles(dut.clk, 5)

    got = int(dut.gpio_dir.value)
    assert got == 0xFFFFFFF0, f"oe_invert: dir 0x{got:08X} != 0xFFFFFFF0"
    rb = await spi.bio_read(0x88)
    assert rb == 0xFFFFFFF0, f"0x88 must read PIN VIEW: 0x{rb:08X}"
    dut._log.info(f"oe_invert=0xF: core set dir all-out, pins show 0x{got:08X} OK")
    await spi.bio_write(0x00, 0)


## ---------------------------------------------------------------------------
@cocotb.test()
async def test_invert_reset_default(dut):
    """All three invert registers must reset to 0 (no-op), or the pads flip at
    power-up. This is the check that matters most for silicon."""
    spi = await setup(dut)
    for name, addr in (("oe_invert", 0x64), ("out_invert", 0x68), ("in_invert", 0x6C)):
        v = await spi.bio_read(addr)
        assert v == 0, f"{name} (0x{addr:02X}) resets to 0x{v:08X}, must be 0"
    dut._log.info("all invert registers reset to 0 — pads do not flip at power-up")

@cocotb.test()
async def debug_in_invert_path(dut):
    """Walk the input path signal by signal to find where inversion is lost."""
    spi = await setup(dut)

    dut.gpio_in.value = 0x0000AAAA
    await ClockCycles(dut.clk, 5)

    def show(tag):
        b = dut.u_bio
        dut._log.info(
            f"{tag}\n"
            f"  gpio_in         = 0x{int(dut.gpio_in.value):08X}\n"
            f"  in_invert       = 0x{int(b.in_invert.value):08X}\n"
            f"  gpio_in_inv     = 0x{int(b.gpio_in_inv.value):08X}\n"
            f"  gpio_in_sync0   = 0x{int(b.gpio_in_sync0.value):08X}\n"
            f"  gpio_in_sync1   = 0x{int(b.gpio_in_sync1.value):08X}\n"
            f"  gpio_in_cleaned = 0x{int(b.gpio_in_cleaned.value):08X}\n"
            f"  gpio_in_snapped = 0x{int(b.gpio_in_snapped.value):08X}\n"
            f"  maybe_snapped   = 0x{int(b.gpio_in_maybe_snapped.value):08X}\n"
            f"  sync_bypass     = 0x{int(b.sync_bypass.value):08X}\n"
            f"  snap_input      = {int(b.snap_input_to_quantum.value)}"
        )

    show("BEFORE invert (in_invert=0, expect all = 0x0000AAAA):")

    await spi.bio_write(0x6C, 0xFFFFFFFF)
    await ClockCycles(dut.clk, 10)

    show("AFTER invert (in_invert=~0, expect inv/sync/cleaned = 0xFFFF5555):")