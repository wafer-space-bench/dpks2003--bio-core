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
# Test 1: Register defaults after reset
# ============================================================
@cocotb.test()
async def test_register_defaults(dut):
    """Verify all registers read correct defaults after reset."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== Register Defaults Test ===")
    all_pass = True

    checks = [
        (0xFC, 0x0B100002, "Chip ID"),
        (0x00, 0x00000000, "Control"),
        (0x84, 0x00000000, "GPIO out"),
        (0x88, 0x00000000, "GPIO dir"),
        (0x0C, 0x00000000, "FIFO levels"),
        (0x64, 0x00000000, "OE invert"),
        (0x68, 0x00000000, "OUT invert"),
        (0x6C, 0x00000000, "IN invert"),
        (0x50, 0x00000000, "QDIV0"),
        (0x54, 0x00000000, "QDIV1"),
        (0x70, 0x00000000, "IRQMASK0"),
        (0x74, 0x00000000, "IRQMASK1"),
    ]

    for addr, expected, name in checks:
        val = await spi.bio_read(addr)
        ok = val == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  {name} (0x{addr:02X}): 0x{val:08X} (expected 0x{expected:08X}) — {status}")
        if not ok:
            all_pass = False

    assert all_pass, "Register defaults mismatch!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — All register defaults correct!")
    dut._log.info("========================================")


# ============================================================
# Test 2: Register write-read consistency
# ============================================================
@cocotb.test()
async def test_register_readback(dut):
    """Write patterns to all writable registers, read back."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== Register Readback Test ===")
    all_pass = True

    test_regs = [
        (0x50, 0x12345678, "QDIV0"),
        (0x54, 0xABCD0000, "QDIV1"),
        (0x64, 0x0000FFFF, "OE_INV"),
        (0x68, 0xFFFF0000, "OUT_INV"),
        (0x6C, 0xA5A5A5A5, "IN_INV"),
        (0x70, 0xDEADBEEF, "IRQMASK0"),
        (0x74, 0xCAFEBABE, "IRQMASK1"),
    ]

    # Write all
    for addr, pattern, name in test_regs:
        await spi.bio_write(addr, pattern)

    # Read back all
    for addr, pattern, name in test_regs:
        readback = await spi.bio_read(addr)
        ok = readback == pattern
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  {name} (0x{addr:02X}): wrote 0x{pattern:08X}, read 0x{readback:08X} — {status}")
        if not ok:
            all_pass = False

    # Test with inverted patterns
    dut._log.info("  --- Inverted patterns ---")
    for addr, pattern, name in test_regs:
        inv_pattern = pattern ^ 0xFFFFFFFF
        await spi.bio_write(addr, inv_pattern)
        readback = await spi.bio_read(addr)
        ok = readback == inv_pattern
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  {name} (0x{addr:02X}): wrote 0x{inv_pattern:08X}, read 0x{readback:08X} — {status}")
        if not ok:
            all_pass = False

    assert all_pass, "Register readback mismatch!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — All register readbacks correct!")
    dut._log.info("========================================")


# ============================================================
# Test 3: FIFO full/empty edge cases
# ============================================================
@cocotb.test()
async def test_fifo_edge_cases(dut):
    """Test FIFO full, empty, overflow, and underflow behavior."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== FIFO Edge Cases Test ===")
    all_pass = True

    # Clear all FIFOs
    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # --- Test empty FIFO level ---
    dut._log.info("Test 1: Empty FIFO level = 0")
    flevel = await spi.bio_read(0x0C)
    fifo0_level = flevel & 0xF
    ok = fifo0_level == 0
    dut._log.info(f"  FIFO0 level: {fifo0_level} — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    # --- Test push and level tracking ---
    dut._log.info("Test 2: Push 1-8 items, check level")
    for i in range(1, 9):
        await spi.bio_write(0x10, 0xAA00 + i)
        await ClockCycles(dut.clk, 10)
        flevel = await spi.bio_read(0x0C)
        fifo0_level = flevel & 0xF
        ok = fifo0_level == i
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  After push {i}: level = {fifo0_level} (expected {i}) — {status}")
        if not ok:
            all_pass = False

    # --- Test FIFO full (push 9th item) ---
    dut._log.info("Test 3: Push into full FIFO")
    await spi.bio_write(0x10, 0xDEAD)
    await ClockCycles(dut.clk, 10)
    flevel = await spi.bio_read(0x0C)
    fifo0_level = flevel & 0xF
    dut._log.info(f"  After overflow push: level = {fifo0_level}")

    # --- Test read back in order ---
    dut._log.info("Test 4: Read back FIFO in order")
    for i in range(1, 9):
        val = await spi.bio_read(0x20)  # RXF0
        expected = 0xAA00 + i
        ok = val == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  FIFO[{i-1}]: 0x{val:08X} (expected 0x{expected:08X}) — {status}")
        if not ok:
            all_pass = False

            # Read the overflow item too
    val = await spi.bio_read(0x20)
    dut._log.info(f"  FIFO[8] (overflow): 0x{val:08X} (expected 0x0000DEAD)")
    ok = val == 0x0000DEAD
    all_pass &= ok

    # --- Test empty after drain ---
    dut._log.info("Test 5: Level after drain")
    flevel = await spi.bio_read(0x0C)
    fifo0_level = flevel & 0xF
    ok = fifo0_level == 0
    dut._log.info(f"  FIFO0 level: {fifo0_level} — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    # --- Test FIFO clear ---
    dut._log.info("Test 6: FIFO clear")
    for i in range(4):
        await spi.bio_write(0x10, 0xBEEF0000 + i)
    await ClockCycles(dut.clk, 10)
    flevel = await spi.bio_read(0x0C)
    dut._log.info(f"  Before clear: level = {flevel & 0xF}")
    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)
    flevel = await spi.bio_read(0x0C)
    ok = (flevel & 0xF) == 0
    dut._log.info(f"  After clear: level = {flevel & 0xF} — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    assert all_pass, "FIFO edge case test failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — FIFO edge cases verified!")
    dut._log.info("========================================")


# ============================================================
# Test 4: Clock gating / FIFO blocking
# ============================================================
@cocotb.test()
async def test_fifo_blocking(dut):
    """Core reads empty FIFO — should stall until data arrives."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== FIFO Blocking (Clock Gate) Test ===")

    # Program: read FIFO0, write to GPIO, loop
    program = [
        0xFFF00293,  # [0x00] li x5, -1
        0x00028C13,  # [0x04] mv x24, x5        (all output)
        0x00028D13,  # [0x08] mv x26, x5        (mask all)
        # loop:
        0x00080513,  # [0x0C] mv x10, x16       (read FIFO0 — BLOCKS)
        0x00050A93,  # [0x10] mv x21, x10       (write GPIO)
        0x00000A13,  # [0x14] mv x20, x0        (quantum)
        0xFF5FF06F,  # [0x18] j -12             (back to 0x0C)
    ]

    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)
    await spi.bio_write(0x50, 50 << 16)
    await spi.bio_write(0x00, 0x00000005)

    # Wait — GPIO should be 0 (core blocked on empty FIFO)
    await ClockCycles(dut.clk, 2000)
    gpio = int(dut.gpio_out.value) & 0xFFFF
    dut._log.info(f"Before push: GPIO = 0x{gpio:04X} (expected 0x0000)")
    assert gpio == 0, f"GPIO should be 0 while blocked, got 0x{gpio:04X}"

    # Push data — core should unblock
    test_values = [0x1234, 0x5678, 0xABCD]
    all_pass = True

    for val in test_values:
        await spi.bio_write(0x10, val)
        await ClockCycles(dut.clk, 500)
        gpio = int(dut.gpio_out.value) & 0xFFFF
        ok = gpio == val
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Pushed 0x{val:04X}, GPIO = 0x{gpio:04X} — {status}")
        if not ok:
            all_pass = False

    await spi.bio_write(0x00, 0x00000000)

    assert all_pass, "FIFO blocking test failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Clock gating/FIFO blocking works!")
    dut._log.info("  Core stalls on empty FIFO")
    dut._log.info("  Core resumes when data arrives")
    dut._log.info("========================================")


# ============================================================
# Test 5: Core restart
# ============================================================
@cocotb.test()
async def test_core_restart(dut):
    """Load program, run, stop, load new program, run again."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== Core Restart Test ===")

    # Program 1: set GPIO to 0x0555
    prog1 = [
        0xFFF00293,  # [0x00] li x5, -1
        0x00028C13,  # [0x04] mv x24, x5        (all output)
        0x00028D13,  # [0x08] mv x26, x5        (mask all)
        0x55500513,  # [0x0C] li x10, 0x555
        0x00050A93,  # [0x10] mv x21, x10       (write GPIO)
        0x00000A13,  # [0x14] mv x20, x0        (quantum)
        0xFF5FF06F,  # [0x18] j -12             (loop)
    ]

    dut._log.info("Loading program 1 (GPIO = 0x0555)...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(prog1):
        await spi.bio_imem_write(addr, instr)
    await spi.bio_write(0x50, 50 << 16)
    await spi.bio_write(0x00, 0x00000005)  # enable + restart
    await ClockCycles(dut.clk, 1000)

    gpio1 = int(dut.gpio_out.value) & 0xFFFF
    dut._log.info(f"  Program 1 GPIO: 0x{gpio1:04X}")

    # Stop core
    await spi.bio_write(0x00, 0x00000000)
    await ClockCycles(dut.clk, 100)

    # Program 2: set GPIO to 0x0AAA
    prog2 = [
        0xFFF00293,  # [0x00] li x5, -1
        0x00028C13,  # [0x04] mv x24, x5
        0x00028D13,  # [0x08] mv x26, x5
        0x6AA00513,  # [0x0C] li x10, 0x6AA      (0xAAA doesn't fit, use 0x6AA)
        0x00050A93,  # [0x10] mv x21, x10
        0x00000A13,  # [0x14] mv x20, x0
        0xFF5FF06F,  # [0x18] j -12
    ]

    dut._log.info("Loading program 2 (GPIO = 0x06AA)...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(prog2):
        await spi.bio_imem_write(addr, instr)
    await spi.bio_write(0x00, 0x00000005)  # enable + restart
    await ClockCycles(dut.clk, 1000)

    gpio2 = int(dut.gpio_out.value) & 0xFFFF
    dut._log.info(f"  Program 2 GPIO: 0x{gpio2:04X}")

    # Verify different outputs
    ok1 = gpio1 == 0x0555
    ok2 = gpio2 == 0x06AA
    ok3 = gpio1 != gpio2

    dut._log.info(f"  Program 1 correct: {'PASS' if ok1 else 'FAIL'}")
    dut._log.info(f"  Program 2 correct: {'PASS' if ok2 else 'FAIL'}")
    dut._log.info(f"  Programs different: {'PASS' if ok3 else 'FAIL'}")

    await spi.bio_write(0x00, 0x00000000)

    assert ok1 and ok2 and ok3, "Core restart test failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Core restart works!")
    dut._log.info("  Stop, reprogram, restart verified")
    dut._log.info("========================================")


# ============================================================
# Test 6: GPIO inversion via host SPI
# ============================================================
@cocotb.test()
async def test_gpio_inversion_host(dut):
    """Test GPIO inversion registers via SPI (host-controlled)."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== GPIO Inversion (Host) Test ===")

    # Program: read FIFO0, write to GPIO, loop
    program = [
        0xFFF00293,  # li x5, -1
        0x00028C13,  # mv x24, x5 (all output)
        0x00028D13,  # mv x26, x5 (mask all)
        0x00080513,  # mv x10, x16 (read FIFO0)
        0x00050A93,  # mv x21, x10 (write GPIO)
        0x00000A13,  # mv x20, x0 (quantum)
        0xFF5FF06F,  # j back
    ]

    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)
    await spi.bio_write(0x50, 50 << 16)

    all_pass = True

    # --- Test output inversion ---
    dut._log.info("Test 1: Output inversion")

    # No inversion first
    await spi.bio_write(0x68, 0x00000000)  # out_invert = 0
    await spi.bio_write(0x00, 0x00000005)
    await spi.bio_write(0x10, 0x00005555)
    await ClockCycles(dut.clk, 500)
    gpio_normal = int(dut.gpio_out.value) & 0xFFFF
    dut._log.info(f"  Normal:   GPIO = 0x{gpio_normal:04X}")

    # Stop, set inversion, restart
    await spi.bio_write(0x00, 0x00000000)
    await ClockCycles(dut.clk, 100)
    await spi.bio_write(0x68, 0x0000FFFF)  # invert lower 16 bits
    await spi.bio_write(0x00, 0x00000005)
    await spi.bio_write(0x10, 0x00005555)
    await ClockCycles(dut.clk, 500)
    gpio_inverted = int(dut.gpio_out.value) & 0xFFFF
    dut._log.info(f"  Inverted: GPIO = 0x{gpio_inverted:04X}")

    ok = gpio_inverted == (gpio_normal ^ 0xFFFF)
    dut._log.info(f"  Output inversion: {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x68, 0x00000000)  # clear inversion

    # --- Test input inversion ---
    dut._log.info("Test 2: Input inversion")

    # Set in_invert and read GPIO input register
    dut.gpio_in.value = 0x00AA
    await ClockCycles(dut.clk, 20)

    await spi.bio_write(0x6C, 0x00000000)  # no inversion
    await ClockCycles(dut.clk, 10)
    gpio_in_normal = await spi.bio_read(0x8C)  # GPIO_I register
    dut._log.info(f"  Normal input:   0x{gpio_in_normal & 0xFFFF:04X}")

    await spi.bio_write(0x6C, 0x0000FFFF)  # invert lower 16
    await ClockCycles(dut.clk, 10)
    gpio_in_inverted = await spi.bio_read(0x8C)
    dut._log.info(f"  Inverted input: 0x{gpio_in_inverted & 0xFFFF:04X}")

    ok = (gpio_in_inverted & 0xFFFF) == ((gpio_in_normal ^ 0xFFFF) & 0xFFFF)
    dut._log.info(f"  Input inversion: {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    await spi.bio_write(0x6C, 0x00000000)  # clear

    assert all_pass, "GPIO inversion test failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — GPIO inversion works!")
    dut._log.info("  Host-controlled via SPI registers")
    dut._log.info("========================================")


# ============================================================
# Test 7: Reset synchronizer verification
# ============================================================
@cocotb.test()
async def test_reset_behavior(dut):
    """Verify chip responds correctly to reset assertion/deassertion."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== Reset Behavior Test ===")

    # Write non-default values to registers
    await spi.bio_write(0x50, 0x12345678)
    await spi.bio_write(0x6C, 0xAAAAAAAA)
    await ClockCycles(dut.clk, 10)

    # Verify they're written
    val1 = await spi.bio_read(0x50)
    val2 = await spi.bio_read(0x6C)
    dut._log.info(f"  Before reset: QDIV0 = 0x{val1:08X}, IN_INV = 0x{val2:08X}")

    # Assert reset
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 10)

    # Deassert reset
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)

    # Verify registers are back to defaults
    val1 = await spi.bio_read(0x50)
    val2 = await spi.bio_read(0x6C)
    dut._log.info(f"  After reset:  QDIV0 = 0x{val1:08X}, IN_INV = 0x{val2:08X}")

    ok1 = val1 == 0
    ok2 = val2 == 0
    dut._log.info(f"  QDIV0 cleared: {'PASS' if ok1 else 'FAIL'}")
    dut._log.info(f"  IN_INV cleared: {'PASS' if ok2 else 'FAIL'}")

    # Verify chip ID still reads correctly after reset
    chip_id = await spi.bio_read(0xFC)
    ok3 = chip_id == 0x0B100002
    dut._log.info(f"  Chip ID: 0x{chip_id:08X} — {'PASS' if ok3 else 'FAIL'}")

    assert ok1 and ok2 and ok3, "Reset behavior test failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Reset clears all registers!")
    dut._log.info("========================================")
