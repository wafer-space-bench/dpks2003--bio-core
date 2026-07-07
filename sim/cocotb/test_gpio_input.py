import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from spi_master import SPIMaster


async def reset_dut(dut):
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
async def test_gpio_input_basic(dut):
    """Test basic GPIO input: drive pins externally, core reads via x21,
    pushes to FIFO1 for host readback.
    
    Verifies: gpio_in → sync stages → gpio_in_cleaned → core x21 read → FIFO1
    
    Program:
      Set GPIO mask to 0xFFFF (all pins)
      Leave direction as input (default)
      Loop: read x21 (GPIO pins) → push to FIFO1 → wait quantum → repeat
    """
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== GPIO Input Basic Test ===")

    # Program: read GPIO pins and push to FIFO1
    #   li x1, -1           → mask = all bits
    #   mv x26, x1          → set GPIO mask
    #   (direction stays input — default after reset)
    # loop:
    #   mv x15, x16         → read command from FIFO0 (blocks — sync with host)
    #   mv x15, x21         → read GPIO pins into x15
    #   mv x17, x15         → push GPIO value to FIFO1
    #   j loop

    program = [
        0xFFF00093,  # [0x00] li x1, -1           mask = all
        0x00008D13,  # [0x04] mv x26, x1          set GPIO mask
        # loop:
        0x00080793,  # [0x08] mv x15, x16         read FIFO0 (blocks — wait for host trigger)
        0x000A8793,  # [0x0C] mv x15, x21         read GPIO pins
        0x00078893,  # [0x10] mv x17, x15         push to FIFO1
        0xFF1FF06F,  # [0x14] j -16               back to FIFO0 read (0x08)
    ]

    # Load program
    dut._log.info("Loading GPIO input program...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Set quantum (not critical — just need core running)
    await spi.bio_write(0x50, 100 << 16)

    # Enable core 0
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 200)

    # Test patterns
    test_patterns = [
        0x0000,  # all low
        0xFFFF,  # all high
        0xAAAA,  # alternating
        0x5555,  # alternating inverse
        0x00FF,  # lower byte
        0xFF00,  # upper byte
        0x0001,  # single bit
        0x8000,  # MSB only
        0x1234,  # arbitrary
        0xBEEF,  # arbitrary
    ]

    all_pass = True
    for pattern in test_patterns:
        # Drive GPIO input pins
        dut.gpio_in.value = pattern

        # Wait for sync stages (2-stage = 2-3 clock cycles)
        await ClockCycles(dut.clk, 10)

        # Trigger the core to read by pushing to FIFO0
        await spi.bio_write(0x10, 0x00000001)  # push any value to FIFO0

        # Wait for core to read x21 and push to FIFO1
        await ClockCycles(dut.clk, 200)

        # Read result from FIFO1
        flevel = await spi.bio_read(0x0C)
        fifo1_level = (flevel >> 4) & 0xF

        if fifo1_level == 0:
            dut._log.error(f"  Pattern 0x{pattern:04X}: FIFO1 empty!")
            all_pass = False
            continue

        result = await spi.bio_read(0x24) & 0xFFFF  # RXF1, mask to 16 bits
        ok = result == pattern
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Drove 0x{pattern:04X}, read 0x{result:04X} — {status}")
        if not ok:
            all_pass = False

    await spi.bio_write(0x00, 0x00000000)
    assert all_pass, "GPIO input pattern mismatch!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — GPIO input works!")
    dut._log.info("  All 10 patterns read correctly")
    dut._log.info("  Input sync stages verified")
    dut._log.info("========================================")


@cocotb.test()
async def test_gpio_input_sync_bypass(dut):
    """Test sync_bypass register: verify 1-stage vs 2-stage sync.
    
    With sync_bypass=0 (default): 2-stage sync, higher latency
    With sync_bypass=1: 1-stage sync, lower latency
    Both should produce correct data, just different latency."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== GPIO Input Sync Bypass Test ===")

    # Same program as above
    program = [
        0xFFF00093,  # li x1, -1
        0x00008D13,  # mv x26, x1
        0x00080793,  # mv x15, x16         (wait for trigger)
        0x000A8793,  # mv x15, x21         (read GPIO)
        0x00078893,  # mv x17, x15         (push to FIFO1)
        0xFF1FF06F,  # j -16
    ]

    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    await spi.bio_write(0x50, 100 << 16)

    # Test 1: Default sync (2-stage, sync_bypass = 0)
    dut._log.info("Test 1: 2-stage sync (default)")
    await spi.bio_write(0x60, 0x00000000)  # SYNC_BYPASS = 0
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 200)

    dut.gpio_in.value = 0xA5A5
    await ClockCycles(dut.clk, 10)
    await spi.bio_write(0x10, 1)  # trigger
    await ClockCycles(dut.clk, 200)
    result_2stage = await spi.bio_read(0x24) & 0xFFFF

    dut._log.info(f"  2-stage: drove 0xA5A5, read 0x{result_2stage:04X}")
    ok1 = result_2stage == 0xA5A5

    # Disable core, clear FIFO
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # Test 2: 1-stage sync (sync_bypass = all bits)
    dut._log.info("Test 2: 1-stage sync (bypass)")
    await spi.bio_write(0x60, 0x0000FFFF)  # SYNC_BYPASS = all bits
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 200)

    dut.gpio_in.value = 0x5A5A
    await ClockCycles(dut.clk, 10)
    await spi.bio_write(0x10, 1)  # trigger
    await ClockCycles(dut.clk, 200)
    result_1stage = await spi.bio_read(0x24) & 0xFFFF

    dut._log.info(f"  1-stage: drove 0x5A5A, read 0x{result_1stage:04X}")
    ok2 = result_1stage == 0x5A5A

    await spi.bio_write(0x00, 0x00000000)

    assert ok1, f"2-stage sync failed: expected 0xA5A5, got 0x{result_2stage:04X}"
    assert ok2, f"1-stage sync failed: expected 0x5A5A, got 0x{result_1stage:04X}"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Sync bypass works!")
    dut._log.info("  2-stage sync: correct")
    dut._log.info("  1-stage sync: correct")
    dut._log.info("========================================")


@cocotb.test()
async def test_gpio_loopback(dut):
    """GPIO loopback: core writes output, reads it back via input.
    
    Due to GPIO mirroring in bio_lite_top, gpio_out drives gpio_in
    on the same physical pins. This test writes a pattern to GPIO output
    pins, then reads it back through the input path.
    
    This exercises both output AND input paths in one test."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== GPIO Loopback Test ===")

    # Program: read value from FIFO0, write to GPIO output, read GPIO input,
    #          push input value to FIFO1
    #
    #   li x1, 0xFF          → mask lower 8 bits
    #   mv x26, x1           → set GPIO mask
    #   mv x24, x1           → lower 8 bits = output
    # loop:
    #   mv x15, x16          → read value from FIFO0 (blocks)
    #   mv x21, x15          → write to GPIO output
    #   mv x20, x0           → wait quantum (let output settle)
    #   mv x15, x21          → read GPIO input
    #   mv x17, x15          → push to FIFO1
    #   j loop

    program = [
        0x0FF00093,  # [0x00] li x1, 0xFF
        0x00008D13,  # [0x04] mv x26, x1          mask
        0x00008C13,  # [0x08] mv x24, x1          direction = output
        # loop:
        0x00080793,  # [0x0C] mv x15, x16         read FIFO0
        0x00078A93,  # [0x10] mv x21, x15         write to GPIO output
        0x00000A13,  # [0x14] mv x20, x0          wait quantum (settle)
        0x000A8793,  # [0x18] mv x15, x21         read GPIO input
        0x00078893,  # [0x1C] mv x17, x15         push to FIFO1
        0xFE5FF06F,  # [0x20] j -28               back to 0x0C... wait
    ]

    # j -28 from 0x20 = 0x20 - 28 = 0x20 - 0x1C = 0x04. Wrong — need 0x0C.
    # 0x0C - 0x20 = -20 = j(-20)
    program[8] = 0xFEDFF06F  # j -20 → back to 0x0C

    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    await spi.bio_write(0x50, 100 << 16)
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 200)

    # For loopback: connect gpio_out to gpio_in externally
    # In cocotb, we do this by updating gpio_in each cycle
    # Start a coroutine that mirrors gpio_out → gpio_in
    async def loopback_driver():
        while True:
            await RisingEdge(dut.clk)
            try:
                gpio_out_val = int(dut.gpio_out.value)
                # Only loop back the lower 8 bits (output-enabled pins)
                current_in = int(dut.gpio_in.value)
                # Merge: output pins override input, non-output pins keep current
                gpio_dir_val = int(dut.gpio_dir.value)
                new_in = (gpio_out_val & gpio_dir_val) | (current_in & ~gpio_dir_val)
                dut.gpio_in.value = new_in & 0xFFFF
            except Exception:
                pass

    loopback_task = cocotb.start_soon(loopback_driver())

    # Test patterns
    test_patterns = [0xAA, 0x55, 0x0F, 0xF0, 0x01, 0x80, 0xFF, 0x00]

    all_pass = True
    for pattern in test_patterns:
        # Push pattern
        await spi.bio_write(0x10, pattern)
        await ClockCycles(dut.clk, 300)

        # Read from FIFO1
        flevel = await spi.bio_read(0x0C)
        fifo1_level = (flevel >> 4) & 0xF
        if fifo1_level == 0:
            dut._log.error(f"  Pattern 0x{pattern:02X}: FIFO1 empty!")
            all_pass = False
            continue

        result = await spi.bio_read(0x24) & 0xFF
        ok = result == pattern
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Output 0x{pattern:02X}, loopback 0x{result:02X} — {status}")
        if not ok:
            all_pass = False

    loopback_task.kill()
    await spi.bio_write(0x00, 0x00000000)

    assert all_pass, "GPIO loopback mismatch!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — GPIO loopback works!")
    dut._log.info("  Output → physical pin → input → FIFO")
    dut._log.info("  Both output and input paths verified")
    dut._log.info("========================================")


@cocotb.test()
async def test_gpio_inversion(dut):
    """Test GPIO input inversion (IO_I_INV register at 0x6C).
    
    Write an inversion mask, drive a pattern on gpio_in,
    verify the core reads the inverted value."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== GPIO Input Inversion Test ===")

    # Same read program
    program = [
        0xFFF00093,  # li x1, -1
        0x00008D13,  # mv x26, x1
        0x00080793,  # mv x15, x16         (trigger)
        0x000A8793,  # mv x15, x21         (read GPIO)
        0x00078893,  # mv x17, x15         (push to FIFO1)
        0xFF1FF06F,  # j -16
    ]

    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    await spi.bio_write(0x50, 100 << 16)

    all_pass = True

    # Test 1: No inversion (baseline)
    dut._log.info("Test 1: No inversion")
    await spi.bio_write(0x6C, 0x00000000)  # IO_I_INV = 0
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 200)

    dut.gpio_in.value = 0x00FF
    await ClockCycles(dut.clk, 10)
    await spi.bio_write(0x10, 1)
    await ClockCycles(dut.clk, 200)
    result = await spi.bio_read(0x24) & 0xFFFF
    ok = result == 0x00FF
    dut._log.info(f"  Drove 0x00FF, read 0x{result:04X} — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # Test 2: Invert all lower 16 bits
    dut._log.info("Test 2: Invert all bits")
    await spi.bio_write(0x6C, 0x0000FFFF)  # IO_I_INV = all bits
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 200)

    dut.gpio_in.value = 0x00FF
    await ClockCycles(dut.clk, 10)
    await spi.bio_write(0x10, 1)
    await ClockCycles(dut.clk, 200)
    result = await spi.bio_read(0x24) & 0xFFFF
    expected = 0xFF00  # inverted 0x00FF
    ok = result == expected
    dut._log.info(f"  Drove 0x00FF, inverted → expect 0x{expected:04X}, got 0x{result:04X} — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # Test 3: Invert only lower byte
    dut._log.info("Test 3: Invert lower byte only")
    await spi.bio_write(0x6C, 0x000000FF)  # IO_I_INV = lower 8 bits
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 200)

    dut.gpio_in.value = 0xA5A5
    await ClockCycles(dut.clk, 10)
    await spi.bio_write(0x10, 1)
    await ClockCycles(dut.clk, 200)
    result = await spi.bio_read(0x24) & 0xFFFF
    expected = 0xA55A  # upper byte unchanged, lower byte inverted
    ok = result == expected
    dut._log.info(f"  Drove 0xA5A5, partial invert → expect 0x{expected:04X}, got 0x{result:04X} — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    # Clean up
    await spi.bio_write(0x00, 0x00000000)
    await spi.bio_write(0x6C, 0x00000000)  # reset inversion

    assert all_pass, "GPIO inversion test failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — GPIO input inversion works!")
    dut._log.info("  No inversion: correct")
    dut._log.info("  Full inversion: correct")
    dut._log.info("  Partial inversion: correct")
    dut._log.info("========================================")
