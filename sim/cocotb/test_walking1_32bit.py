import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from spi_master import SPIMaster


async def reset_dut(dut):
    """Common reset sequence."""
    cocotb.start_soon(Clock(dut.clk, 40, units="ns").start())
    dut.reset_n.value = 0
    dut.spi_cs_n.value = 1
    dut.spi_sclk.value = 0
    dut.spi_mosi.value = 0
    dut.gpio_in.value = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)


# ============================================================
# Test 1: Upper GPIO output (bits 16-31)
# ============================================================
#@cocotb.test()
#async def test_upper_gpio_output(dut):
#    """Verify GPIO bits 16-31 output correctly via FIFO echo."""
#    await reset_dut(dut)
#    spi = SPIMaster(dut, spi_period_ns=1000)
#
#    dut._log.info("=== Upper GPIO Output Test ===")
#
#    # Program: read FIFO0, write to GPIO, loop
#    program = [
#        0xFFF00293,  # li x5, -1
#        0x00028C13,  # mv x24, x5 (all 32 bits output)
#        0x00028D13,  # mv x26, x5 (mask all 32 bits)
#        0x00080513,  # mv x10, x16 (read FIFO0)
#        0x00050A93,  # mv x21, x10 (write GPIO)
#        0x00000A13,  # mv x20, x0 (quantum)
#        0xFF5FF06F,  # j back
#    ]
#
#    await spi.bio_write(0xE0, 0x00000000)
#    for addr, instr in enumerate(program):
#        await spi.bio_imem_write(addr, instr)
#    await spi.bio_write(0x50, 50 << 16)
#    await spi.bio_write(0x00, 0x00000005)
#
#    all_pass = True
#
#    # Test patterns that exercise upper bits
#    test_patterns = [
#        (0xFFFF0000, "upper 16 all ones"),
#        (0x80000000, "bit 31 only"),
#        (0x00010000, "bit 16 only"),
#        (0xA5A50000, "upper alternating"),
#        (0x0000A5A5, "lower alternating"),
#        (0xA5A5A5A5, "full alternating"),
#        (0xFFFFFFFF, "all ones"),
#        (0x12345678, "mixed pattern"),
#    ]
#
#    for pattern, desc in test_patterns:
#        await spi.bio_write(0x10, pattern)
#        await ClockCycles(dut.clk, 500)
#        gpio = int(dut.gpio_out.value)
#        ok = gpio == pattern
#        status = "PASS" if ok else "FAIL"
#        dut._log.info(f"  {desc}: wrote 0x{pattern:08X}, got 0x{gpio:08X} — {status}")
#        if not ok:
#            all_pass = False
#
#    await spi.bio_write(0x00, 0x00000000)
#
#    assert all_pass, "Upper GPIO output test failed!"
#
#    dut._log.info("")
#    dut._log.info("========================================")
#    dut._log.info("  PASS — Full 32-bit GPIO output works!")
#    dut._log.info("========================================")
#
#
## ============================================================
## Test 2: Upper GPIO input (bits 16-31)
## ============================================================
#@cocotb.test()
#async def test_upper_gpio_input(dut):
#    """Verify GPIO bits 16-31 input correctly."""
#    await reset_dut(dut)
#    spi = SPIMaster(dut, spi_period_ns=1000)
#
#    dut._log.info("=== Upper GPIO Input Test ===")
#
#    all_pass = True
#
#    test_patterns = [
#        0x00000000,
#        0x0000FFFF,
#        0xFFFF0000,
#        0xFFFFFFFF,
#        0x80000001,
#        0xA5A5A5A5,
#        0x5A5A5A5A,
#        0x12345678,
#        0x00010000,
#        0x80000000,
#    ]
#
#    for pattern in test_patterns:
#        dut.gpio_in.value = pattern
#        await ClockCycles(dut.clk, 20)
#        readback = await spi.bio_read(0x8C)  # GPIO_I register
#        ok = readback == pattern
#        status = "PASS" if ok else "FAIL"
#        dut._log.info(f"  Drove 0x{pattern:08X}, read 0x{readback:08X} — {status}")
#        if not ok:
#            all_pass = False
#
#    assert all_pass, "Upper GPIO input test failed!"
#
#    dut._log.info("")
#    dut._log.info("========================================")
#    dut._log.info("  PASS — Full 32-bit GPIO input works!")
#    dut._log.info("========================================")
#
#
# ============================================================
# Test 3: 32-bit walking 1
# ============================================================
@cocotb.test()
async def test_walking_1_32bit(dut):
    """Walking 1-bit pattern across all 32 GPIO pins."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== 32-bit Walking-1 Test ===")

    # Program: read FIFO0, write to GPIO, loop
    program = [
        0xFFF00293,  # li x5, -1
        0x00028C13,  # mv x24, x5
        0x00028D13,  # mv x26, x5
        0x00080513,  # mv x10, x16 (read FIFO0)
        0x00050A93,  # mv x21, x10
        0x00000A13,  # mv x20, x0
        0xFF5FF06F,  # j back
    ]

    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)
    await spi.bio_write(0x50, 50 << 16)
    await spi.bio_write(0x00, 0x00000005)

    all_pass = True

    for bit in range(32):
        pattern = 1 << bit
        await spi.bio_write(0x10, pattern)
        await ClockCycles(dut.clk, 500)
        gpio = int(dut.gpio_out.value)
        ok = gpio == pattern
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Bit {bit:2d}: 0x{pattern:08X} → 0x{gpio:08X} — {status}")
        if not ok:
            all_pass = False

    await spi.bio_write(0x00, 0x00000000)

    assert all_pass, "Walking-1 32-bit test failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Walking-1 across all 32 bits!")
    dut._log.info("========================================")


# ============================================================
# Test 4: GPIO direction upper bits
# ============================================================
#@cocotb.test()
#async def test_gpio_direction_32bit(dut):
#    """Verify GPIO direction register works for all 32 bits."""
#    await reset_dut(dut)
#    spi = SPIMaster(dut, spi_period_ns=1000)
#
#    dut._log.info("=== 32-bit GPIO Direction Test ===")
#
#    all_pass = True
#
#    test_patterns = [
#        (0x0000FFFF, "lower 16 output"),
#        (0xFFFF0000, "upper 16 output"),
#        (0xFFFFFFFF, "all output"),
#        (0x00000000, "all input"),
#        (0xA5A5A5A5, "alternating"),
#        (0x80000001, "bit 0 + bit 31"),
#    ]
#
#    for pattern, desc in test_patterns:
#        # Set direction via SPI (host path)
#        await spi.bio_write(0x88, pattern)
#        await ClockCycles(dut.clk, 10)
#        readback = await spi.bio_read(0x88)
#
#        # Direction register might not be directly writable via host SPI
#        # It's set by the core via x24. Let's check via core instead.
#        dut._log.info(f"  {desc}: wrote 0x{pattern:08X}, read 0x{readback:08X}")
#
#    # Use core to set direction
#    # Program: set direction to pattern from FIFO, then write GPIO
#    program = [
#        0x00080513,  # mv x10, x16 (read direction from FIFO0)
#        0x00050C13,  # mv x24, x10 (set direction)
#        0xFFF00293,  # li x5, -1
#        0x00028D13,  # mv x26, x5 (mask all)
#        0x00000A13,  # mv x20, x0 (quantum)
#        0xFF5FF06F,  # j back to quantum
#    ]
#
#    await spi.bio_write(0x00, 0x00000000)  # stop
#    await spi.bio_write(0xE0, 0x00000000)
#    for addr, instr in enumerate(program):
#        await spi.bio_imem_write(addr, instr)
#    await spi.bio_write(0x50, 50 << 16)
#
#    dir_patterns = [
#        0x0000FFFF,
#        0xFFFF0000,
#        0xFFFFFFFF,
#        0xA5A5A5A5,
#    ]
#
#    for pattern in dir_patterns:
#        await spi.bio_write(0x00, 0x00000005)  # enable + restart
#        await spi.bio_write(0x10, pattern)  # push direction value
#        await ClockCycles(dut.clk, 1000)
#
#        dir_val = await spi.bio_read(0x88)
#        ok = dir_val == pattern
#        status = "PASS" if ok else "FAIL"
#        dut._log.info(f"  Core set dir 0x{pattern:08X}, read 0x{dir_val:08X} — {status}")
#        if not ok:
#            all_pass = False
#
#        await spi.bio_write(0x00, 0x00000000)
#        await ClockCycles(dut.clk, 100)
#
#    assert all_pass, "GPIO direction 32-bit test failed!"
#
#    dut._log.info("")
#    dut._log.info("========================================")
#    dut._log.info("  PASS — 32-bit GPIO direction works!")
#    dut._log.info("========================================")
#

## ============================================================
## Test 5: GPIO inversion on all 32 bits
## ============================================================
#@cocotb.test()
#async def test_gpio_inversion_32bit(dut):
#    """Verify GPIO inversion works on all 32 bits."""
#    await reset_dut(dut)
#    spi = SPIMaster(dut, spi_period_ns=1000)
#
#    dut._log.info("=== 32-bit GPIO Inversion Test ===")
#
#    all_pass = True
#
#    # --- Input inversion ---
#    dut._log.info("Input inversion:")
#
#    input_patterns = [
#        (0xA5A5A5A5, 0xFFFFFFFF, 0x5A5A5A5A),
#        (0x12345678, 0x0000FFFF, 0x1234A987),
#        (0x00000000, 0xFFFFFFFF, 0xFFFFFFFF),
#        (0xFFFFFFFF, 0x80000001, 0x7FFFFFFE),
#    ]
#
#    for gpio_val, inv_mask, expected in input_patterns:
#        dut.gpio_in.value = gpio_val
#        await spi.bio_write(0x6C, inv_mask)
#        await ClockCycles(dut.clk, 20)
#        readback = await spi.bio_read(0x8C)
#        ok = readback == expected
#        status = "PASS" if ok else "FAIL"
#        dut._log.info(f"  in=0x{gpio_val:08X} inv=0x{inv_mask:08X} → 0x{readback:08X} (expected 0x{expected:08X}) — {status}")
#        if not ok:
#            all_pass = False
#
#    await spi.bio_write(0x6C, 0x00000000)  # clear
#
#    # --- Output inversion ---
#    dut._log.info("Output inversion:")
#
#    # Program: read FIFO0, write to GPIO
#    program = [
#        0xFFF00293,  # li x5, -1
#        0x00028C13,  # mv x24, x5
#        0x00028D13,  # mv x26, x5
#        0x00080513,  # mv x10, x16
#        0x00050A93,  # mv x21, x10
#        0x00000A13,  # mv x20, x0
#        0xFF5FF06F,  # j back
#    ]
#
#    await spi.bio_write(0xE0, 0x00000000)
#    for addr, instr in enumerate(program):
#        await spi.bio_imem_write(addr, instr)
#    await spi.bio_write(0x50, 50 << 16)
#
#    output_patterns = [
#        (0xA5A5A5A5, 0x00000000, 0xA5A5A5A5),  # no inversion
#        (0xA5A5A5A5, 0xFFFFFFFF, 0x5A5A5A5A),  # full inversion
#        (0xA5A5A5A5, 0xFFFF0000, 0x5A5AA5A5),  # upper inversion only
#    ]
#
#    for data, inv_mask, expected in output_patterns:
#        await spi.bio_write(0x00, 0x00000000)
#        await ClockCycles(dut.clk, 100)
#        await spi.bio_write(0x68, inv_mask)
#        await spi.bio_write(0x00, 0x00000005)
#        await spi.bio_write(0x10, data)
#        await ClockCycles(dut.clk, 500)
#        gpio = int(dut.gpio_out.value)
#        ok = gpio == expected
#        status = "PASS" if ok else "FAIL"
#        dut._log.info(f"  data=0x{data:08X} inv=0x{inv_mask:08X} → 0x{gpio:08X} (expected 0x{expected:08X}) — {status}")
#        if not ok:
#            all_pass = False
#
#    await spi.bio_write(0x00, 0x00000000)
#    await spi.bio_write(0x68, 0x00000000)
#
#    assert all_pass, "GPIO inversion 32-bit test failed!"
#
#    dut._log.info("")
#    dut._log.info("========================================")
#    dut._log.info("  PASS — 32-bit GPIO inversion works!")
#    dut._log.info("========================================")
#
#
## ============================================================
## Test 6: Cross-side GPIO (EAST + WEST pads)
## ============================================================
#@cocotb.test()
#async def test_cross_side_gpio(dut):
#    """Verify GPIO works on both EAST (0-18) and WEST (19-31) sides."""
#    await reset_dut(dut)
#    spi = SPIMaster(dut, spi_period_ns=1000)
#
#    dut._log.info("=== Cross-Side GPIO Test ===")
#
#    all_pass = True
#
#    # Drive specific patterns that exercise both sides
#    # EAST: GPIO[0:18] = bidir[3:21]
#    # WEST: GPIO[19:31] = bidir[22:34]
#
#    patterns = [
#        (0x00000001, "GPIO[0] — EAST side"),
#        (0x00040000, "GPIO[18] — EAST side (last)"),
#        (0x00080000, "GPIO[19] — WEST side (first)"),
#        (0x80000000, "GPIO[31] — WEST side (last)"),
#        (0x00050001, "GPIO[0]+GPIO[18] — EAST only"),
#        (0x80080000, "GPIO[19]+GPIO[31] — WEST only"),
#        (0x80040001, "GPIO[0]+GPIO[18]+GPIO[31] — both sides"),
#    ]
#
#    # Test input path
#    dut._log.info("Input across both sides:")
#    for pattern, desc in patterns:
#        dut.gpio_in.value = pattern
#        await ClockCycles(dut.clk, 20)
#        readback = await spi.bio_read(0x8C)
#        ok = readback == pattern
#        status = "PASS" if ok else "FAIL"
#        dut._log.info(f"  {desc}: 0x{pattern:08X} → 0x{readback:08X} — {status}")
#        if not ok:
#            all_pass = False
#
#    # Test output path
#    dut._log.info("Output across both sides:")
#
#    program = [
#        0xFFF00293,  # li x5, -1
#        0x00028C13,  # mv x24, x5
#        0x00028D13,  # mv x26, x5
#        0x00080513,  # mv x10, x16
#        0x00050A93,  # mv x21, x10
#        0x00000A13,  # mv x20, x0
#        0xFF5FF06F,  # j back
#    ]
#
#    await spi.bio_write(0xE0, 0x00000000)
#    for addr, instr in enumerate(program):
#        await spi.bio_imem_write(addr, instr)
#    await spi.bio_write(0x50, 50 << 16)
#    await spi.bio_write(0x00, 0x00000005)
#
#    for pattern, desc in patterns:
#        await spi.bio_write(0x10, pattern)
#        await ClockCycles(dut.clk, 500)
#        gpio = int(dut.gpio_out.value)
#        ok = gpio == pattern
#        status = "PASS" if ok else "FAIL"
#        dut._log.info(f"  {desc}: 0x{pattern:08X} → 0x{gpio:08X} — {status}")
#        if not ok:
#            all_pass = False
#
#    await spi.bio_write(0x00, 0x00000000)
#
#    assert all_pass, "Cross-side GPIO test failed!"
#
#    dut._log.info("")
#    dut._log.info("========================================")
#    dut._log.info("  PASS — GPIO works on both EAST + WEST!")
#    dut._log.info("========================================")
#