# test_imem_full_range.py
#
# Coverage for the SPI IMEM addressing fix:
#   - cmd 0x04/0x05 = IMEM write/read, words 0-255   (addr[8]=0)
#   - cmd 0x06/0x07 = IMEM write/read, words 256-511 (addr[8]=1)
#   - burst writes auto-increment and must cross the 255->256 boundary
#   - both cores' IMEMs, selected via reg 0xE0
#
# These are exactly the cases the original 8-bit address path could not
# reach (words 256-511) or silently corrupted (burst wrap at 256).

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from spi_master import SPIMaster


async def setup_dut(dut):
    """Clock, reset, SPI master, cores disabled."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())  # 25 MHz sysclk

    dut.reset_n.value = 0
    dut.gpio_in.value = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)

    spi = SPIMaster(dut)

    # Make sure both cores are disabled — host owns the IMEMs
    await spi.bio_write(0x00, 0x00000000)
    await ClockCycles(dut.clk, 5)
    return spi


def pattern(core, addr):
    """Distinct, address- and core-dependent 32-bit test value."""
    return (0xA0000000 | (core << 24) | (addr << 8) | (addr & 0xFF)) & 0xFFFFFFFF


@cocotb.test()
async def test_imem_corner_addresses(dut):
    """Single write/read at words 0, 1, 254, 255, 256, 257, 300, 510, 511 (core 0)."""
    spi = await setup_dut(dut)
    await spi.bio_write(0xE0, 0)  # core 0 IMEM

    addrs = [0, 1, 254, 255, 256, 257, 300, 510, 511]

    for a in addrs:
        await spi.bio_imem_write(a, pattern(0, a))

    errors = 0
    for a in addrs:
        got = await spi.bio_imem_read(a)
        exp = pattern(0, a)
        if got != exp:
            dut._log.error(f"word {a:3d}: expected 0x{exp:08X}, got 0x{got:08X}")
            errors += 1
        else:
            dut._log.info(f"word {a:3d}: 0x{got:08X} OK")

    # Aliasing check: if addr[8] were dropped, word 256 would alias word 0,
    # 257 -> 1, 511 -> 255. Verify the low-half words still hold THEIR values.
    for lo, hi in [(0, 256), (1, 257), (255, 511)]:
        got_lo = await spi.bio_imem_read(lo)
        got_hi = await spi.bio_imem_read(hi)
        assert got_lo != got_hi, \
            f"ALIASING: word {lo} == word {hi} == 0x{got_lo:08X} (addr[8] dropped?)"

    assert errors == 0, f"{errors} corner-address mismatches"


@cocotb.test()
async def test_imem_burst_boundary_crossing(dut):
    """One burst starting at word 250 must cross into 256+ without wrapping.

    This is the exact case the old 8-bit addr_reg failed: at 255->256 it
    wrapped to word 0 and overwrote the start of memory.
    """
    spi = await setup_dut(dut)
    await spi.bio_write(0xE0, 0)

    # Canary at word 0 — burst wrap would destroy it
    canary = 0xC0DE0000
    await spi.bio_imem_write(0, canary)

    start = 250
    words = [pattern(0, start + i) ^ 0x5A5A0000 for i in range(12)]  # 250..261
    await spi.bio_imem_burst_write(start, words)

    errors = 0
    for i, w in enumerate(words):
        a = start + i
        got = await spi.bio_imem_read(a)
        if got != w:
            dut._log.error(f"burst word {a}: expected 0x{w:08X}, got 0x{got:08X}")
            errors += 1
        else:
            dut._log.info(f"burst word {a}: 0x{got:08X} OK")

    got0 = await spi.bio_imem_read(0)
    assert got0 == canary, \
        f"CANARY DESTROYED: word 0 = 0x{got0:08X} (burst wrapped at 256!)"

    assert errors == 0, f"{errors} burst mismatches across the 255->256 boundary"


@cocotb.test()
async def test_imem_burst_full_memory(dut):
    """Burst-load all 512 words in one CS assertion, verify a sample."""
    spi = await setup_dut(dut)
    await spi.bio_write(0xE0, 0)

    words = [(0xF0000000 | (i << 16) | (0xFFFF - i)) & 0xFFFFFFFF for i in range(512)]
    await spi.bio_imem_burst_write(0, words)

    # Full readback is slow over bit-banged SPI; sample densely enough
    sample = [0, 1, 127, 128, 255, 256, 257, 383, 384, 509, 510, 511]
    errors = 0
    for a in sample:
        got = await spi.bio_imem_read(a)
        if got != words[a]:
            dut._log.error(f"word {a:3d}: expected 0x{words[a]:08X}, got 0x{got:08X}")
            errors += 1
    assert errors == 0, f"{errors} mismatches after full-memory burst load"


@cocotb.test()
async def test_imem_two_cores_isolated(dut):
    """Upper-half access works on core 1 too, and the cores' IMEMs are isolated."""
    spi = await setup_dut(dut)

    addrs = [0, 255, 256, 300, 511]

    # Fill core 0
    await spi.bio_write(0xE0, 0)
    for a in addrs:
        await spi.bio_imem_write(a, pattern(0, a))

    # Fill core 1 with DIFFERENT values
    await spi.bio_write(0xE0, 1)
    for a in addrs:
        await spi.bio_imem_write(a, pattern(1, a))

    # Core 1 readback
    errors = 0
    for a in addrs:
        got = await spi.bio_imem_read(a)
        if got != pattern(1, a):
            dut._log.error(f"core1 word {a}: expected 0x{pattern(1,a):08X}, got 0x{got:08X}")
            errors += 1

    # Core 0 must be untouched by core 1 writes
    await spi.bio_write(0xE0, 0)
    for a in addrs:
        got = await spi.bio_imem_read(a)
        if got != pattern(0, a):
            dut._log.error(f"core0 word {a} CORRUPTED by core1 access: got 0x{got:08X}")
            errors += 1

    assert errors == 0, f"{errors} cross-core errors"
