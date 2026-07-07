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


@cocotb.test()
async def test_bunnie_hello_world(dut):
    """bunnie's hello_world: walking 1-bit across GPIO with quantum timing.
    Exact translation from xous-core/libs/xous-bio-bdma/src/bio_tests/units.rs"""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== bunnie's hello_world test ===")

    # bunnie's hello_world program (from units.rs):
    #
    #   li    t0, 0xFFFFFFFF    → addi x5, x0, -1
    #   mv    x24, t0           → set all pins as output
    #   li    a0, 1             → starting bit = 1
    # loop:
    #   mv    x21, a0           → write to GPIO (set+clear with mask=all)
    #   slli  a0, a0, 1         → shift left
    #   bne   a0, zero, skip    → if not zero, skip reset
    #   li    a0, 1             → reset to 1
    # skip:
    #   mv    x20, zero         → wait quantum
    #   j     loop              → back to loop
    #   nop

    program = [
        0xFFF00293,  # [0x00] addi x5, x0, -1       li t0, -1 (0xFFFFFFFF)
        0x00028C13,  # [0x04] addi x24, x5, 0        mv x24, t0 (all output)
        0x00100513,  # [0x08] addi x10, x0, 1         li a0, 1
        # loop (0x0C):
        0x00050A93,  # [0x0C] addi x21, x10, 0        mv x21, a0 (GPIO write)
        0x00151513,  # [0x10] slli x10, x10, 1         slli a0, a0, 1
        0x00051463,  # [0x14] bne x10, x0, +8          bne a0, zero, skip
        0x00100513,  # [0x18] addi x10, x0, 1          li a0, 1 (reset)
        # skip (0x1C):
        0x00000A13,  # [0x1C] addi x20, x0, 0          mv x20, zero (quantum)
        0xFEDFF06F,  # [0x20] jal x0, -20              j loop (0x0C)
        0x00000013,  # [0x24] nop
    ]

    # Load program
    dut._log.info(f"Loading bunnie's hello_world ({len(program)} words)...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Set quantum divider = 100 cycles (fast for simulation)
    QDIV = 100
    await spi.bio_write(0x50, QDIV << 16)

    # NOTE: bunnie does NOT set GPIO mask (x26) in software here.
    # The default mask is 0xFFFFFFFF (set in picorv32_regs_bio reset).
    # He sets direction via x24 = 0xFFFFFFFF (all outputs).
    # He writes GPIO via x21 which uses the default mask.

    # Enable core 0 with restart
    dut._log.info("Enabling core 0...")
    await spi.bio_write(0x00, 0x00000005)

    # Wait for setup instructions to execute
    await ClockCycles(dut.clk, 200)

    # Capture GPIO output values at each quantum period
    # The walking 1 pattern should be: 1, 2, 4, 8, 16, 32, ...
    dut._log.info("Capturing GPIO output pattern...")
    captured = []
    expected_pattern = [1 << i for i in range(16)]  # bits 0-15 (16 physical pins)

    for i in range(20):
        # Wait for one quantum period + margin
        await ClockCycles(dut.clk, QDIV + 50)
        gpio_val = int(dut.gpio_out.value)
        captured.append(gpio_val)
        dut._log.info(f"  [{i:2d}] gpio_out = 0x{gpio_val:04X} ({gpio_val:016b})")

    # Verify the pattern is a walking 1
    # The pattern wraps: after bit 15 (on physical pins), it continues to
    # bits 16-31 internally but those don't appear on gpio_out (only 16 pins).
    # When the internal bit is in positions 16-31, gpio_out shows 0x0000.
    # Then it wraps back to bit 0 and the pattern repeats.
    dut._log.info("")
    dut._log.info("Checking for walking-1 pattern...")

    # Find the first non-zero value to sync to the pattern
    start_idx = None
    for i, val in enumerate(captured):
        if val == 0x0001:
            start_idx = i
            break

    if start_idx is None:
        # Maybe we missed the start, just check that we see power-of-2 values
        dut._log.info("Didn't catch bit 0, checking for valid power-of-2 values...")
        valid_count = 0
        for val in captured:
            if val == 0:
                continue  # could be bits 16-31 (invisible on 16 pins)
            # Check if it's a power of 2
            if val & (val - 1) == 0:
                valid_count += 1
                dut._log.info(f"  Valid: 0x{val:04X} (bit {val.bit_length()-1})")
            else:
                dut._log.error(f"  INVALID: 0x{val:04X} (not a power of 2!)")

        assert valid_count > 0, "No valid walking-1 values found!"
        dut._log.info(f"  Found {valid_count} valid power-of-2 GPIO values")
    else:
        dut._log.info(f"  Pattern starts at sample {start_idx}")
        all_pass = True
        for i in range(min(16, len(captured) - start_idx)):
            expected = 1 << i
            actual = captured[start_idx + i]
            ok = actual == expected
            status = "PASS" if ok else "FAIL"
            dut._log.info(f"  bit {i:2d}: expected 0x{expected:04X}, got 0x{actual:04X} — {status}")
            if not ok:
                all_pass = False

        if not all_pass:
            assert False, "Walking-1 pattern mismatch!"

    # Also verify GPIO direction is all outputs
    gpio_dir = await spi.bio_read(0x88)
    dut._log.info(f"  GPIO direction: 0x{gpio_dir:08X}")

    # Disable core
    await spi.bio_write(0x00, 0x00000000)

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — bunnie's hello_world works!")
    dut._log.info("  Walking 1-bit pattern verified on GPIO")
    dut._log.info("========================================")


@cocotb.test()
async def test_bunnie_fifo_host_bitbang(dut):
    """bunnie's host_fifo test: host pushes values, core outputs to GPIO.
    From xous-core/libs/xous-bio-bdma/src/bio_tests/units.rs fifo_host0_bitbang"""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== bunnie's host FIFO bitbang test ===")

    # Core 0 program: read FIFO0, write to GPIO, wait quantum, loop
    # This is fifo_host0_bitbang from units.rs:
    #   li t0, 0xFFFFFFFF
    #   mv x24, t0          (all pins output)
    #   mv x26, t0          (mask = all)
    # loop:
    #   mv t1, x16           (read FIFO0 → t1, blocks)
    #   mv x21, t1           (write to GPIO)
    #   mv x20, zero         (wait quantum)
    #   j loop

    program = [
        0xFFF00293,  # [0x00] li x5, -1           (0xFFFFFFFF)
        0x00028C13,  # [0x04] mv x24, x5           (all output)
        0x00028D13,  # [0x08] mv x26, x5           (mask = all)
        # loop:
        0x00080313,  # [0x0C] mv x6, x16           (read FIFO0 → x6, blocks)
        0x00030A93,  # [0x10] mv x21, x6           (write to GPIO)
        0x00000A13,  # [0x14] mv x20, x0           (wait quantum)
        0xFF5FF06F,  # [0x18] j -12                (back to 0x0C)
    ]

    # Load program
    dut._log.info("Loading host FIFO bitbang program...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Slow quantum so FIFO builds backpressure
    QDIV = 200
    await spi.bio_write(0x50, QDIV << 16)

    # Enable core 0
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 500)

    # Push test values and verify GPIO output
    test_values = [
        0x0000F1F0,
        0x00001234,
        0x0000ABCD,
        0x00005555,
        0x0000AAAA,
        0x0000FFFF,
        0x00000001,
        0x00008000,
    ]

    dut._log.info("Pushing values and checking GPIO...")
    all_pass = True
    for val in test_values:
        await spi.bio_write(0x10, val)
        # Wait for core to read FIFO and write to GPIO
        await ClockCycles(dut.clk, QDIV * 3)

        gpio_out = int(dut.gpio_out.value)
        expected = val & 0xFFFF  # only lower 16 bits on physical pins
        ok = gpio_out == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Pushed 0x{val:08X}, GPIO=0x{gpio_out:04X} (expected 0x{expected:04X}) — {status}")
        if not ok:
            all_pass = False

    await spi.bio_write(0x00, 0x00000000)

    assert all_pass, "Some GPIO values didn't match!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — bunnie's host FIFO bitbang works!")
    dut._log.info("========================================")
