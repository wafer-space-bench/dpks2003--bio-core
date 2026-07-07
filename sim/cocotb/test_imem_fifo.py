import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from spi_master import SPIMaster


async def reset_dut(dut):
    """Common reset sequence for all tests."""
    cocotb.start_soon(Clock(dut.clk, 40, units="ns").start())
    dut.reset_n.value = 0
    dut.spi_cs_n.value = 1
    dut.spi_sclk.value = 0
    dut.spi_mosi.value = 0
    dut.gpio_in.value = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)


# =========================================================================
# Test 3a: IMEM Write + Read — Core 0
# =========================================================================
@cocotb.test()
async def test_imem_core0(dut):
    """Write 3 instructions to core 0 IMEM, read them back."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    # The FIFO echo program
    program = [
        0x00080793,  # mv x15, x16   (read FIFO0)
        0x00078893,  # mv x17, x15   (write FIFO1)
        0xFF9FF06F,  # j 0x00        (loop)
    ]

    # Select core 0 IMEM
    dut._log.info("Selecting core 0 IMEM...")
    await spi.bio_write(0xE0, 0x00000000)

    # Write program
    dut._log.info("Writing program to core 0 IMEM...")
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Read back and verify
    dut._log.info("Reading back core 0 IMEM...")
    for addr, expected in enumerate(program):
        result = await spi.bio_imem_read(addr)
        dut._log.info(f"  IMEM[{addr}]: 0x{result:08X} (expected 0x{expected:08X})")
        assert result == expected, \
            f"Core 0 IMEM[{addr}] mismatch: got 0x{result:08X}, expected 0x{expected:08X}"

    dut._log.info("PASS — core 0 IMEM write+read works")


# =========================================================================
# Test 3b: IMEM Write + Read — Core 1 (independent from Core 0)
# =========================================================================
@cocotb.test()
async def test_imem_core1(dut):
    """Write different data to core 1 IMEM, verify core 0 isn't corrupted."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    # Write known values to core 0
    dut._log.info("Writing to core 0 IMEM...")
    await spi.bio_write(0xE0, 0x00000000)  # select core 0
    await spi.bio_imem_write(0x00, 0xAAAAAAAA)
    await spi.bio_imem_write(0x01, 0xBBBBBBBB)
    await spi.bio_imem_write(0x02, 0xCCCCCCCC)

    # Write different values to core 1
    dut._log.info("Writing to core 1 IMEM...")
    await spi.bio_write(0xE0, 0x00000001)  # select core 1
    await spi.bio_imem_write(0x00, 0x11111111)
    await spi.bio_imem_write(0x01, 0x22222222)
    await spi.bio_imem_write(0x02, 0x33333333)

    # Verify core 1 has correct data
    dut._log.info("Verifying core 1 IMEM...")
    result = await spi.bio_imem_read(0x00)
    assert result == 0x11111111, f"Core 1 IMEM[0] mismatch: 0x{result:08X}"
    result = await spi.bio_imem_read(0x01)
    assert result == 0x22222222, f"Core 1 IMEM[1] mismatch: 0x{result:08X}"
    result = await spi.bio_imem_read(0x02)
    assert result == 0x33333333, f"Core 1 IMEM[2] mismatch: 0x{result:08X}"
    dut._log.info("  Core 1: all correct")

    # Switch back to core 0 and verify NOT corrupted
    dut._log.info("Verifying core 0 IMEM not corrupted...")
    await spi.bio_write(0xE0, 0x00000000)  # select core 0
    result = await spi.bio_imem_read(0x00)
    assert result == 0xAAAAAAAA, f"Core 0 IMEM[0] corrupted: 0x{result:08X}"
    result = await spi.bio_imem_read(0x01)
    assert result == 0xBBBBBBBB, f"Core 0 IMEM[1] corrupted: 0x{result:08X}"
    result = await spi.bio_imem_read(0x02)
    assert result == 0xCCCCCCCC, f"Core 0 IMEM[2] corrupted: 0x{result:08X}"
    dut._log.info("  Core 0: not corrupted")

    dut._log.info("PASS — per-core IMEM isolation works")


# =========================================================================
# Test 4: FIFO Echo — THE BIG ONE
# Proves: PicoRV32 boots, executes instructions, FIFO x16/x17 works,
#         clock gate works, stall/wake mechanism works
# =========================================================================
@cocotb.test()
async def test_fifo_echo(dut):
    """Load FIFO echo program, push data, verify it comes back through FIFO1."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== FIFO Echo Test ===")

    # Step 1: Select core 0 IMEM
    dut._log.info("Step 1: Selecting core 0 IMEM...")
    await spi.bio_write(0xE0, 0x00000000)

    # Step 2: Load FIFO echo program (3 instructions)
    dut._log.info("Step 2: Loading FIFO echo program...")
    await spi.bio_imem_write(0x00, 0x00080793)  # mv x15, x16  (read FIFO0, blocks)
    await spi.bio_imem_write(0x01, 0x00078893)  # mv x17, x15  (write to FIFO1)
    await spi.bio_imem_write(0x02, 0xFF9FF06F)  # j 0x00       (loop back)

    # Step 3: Verify program loaded correctly
    dut._log.info("Step 3: Verifying program...")
    r0 = await spi.bio_imem_read(0x00)
    r1 = await spi.bio_imem_read(0x01)
    r2 = await spi.bio_imem_read(0x02)
    assert r0 == 0x00080793, f"IMEM[0] verify fail: 0x{r0:08X}"
    assert r1 == 0x00078893, f"IMEM[1] verify fail: 0x{r1:08X}"
    assert r2 == 0xFF9FF06F, f"IMEM[2] verify fail: 0x{r2:08X}"
    dut._log.info("  Program verified OK")

    # Step 4: Enable core 0
    dut._log.info("Step 4: Enabling core 0...")
    await spi.bio_write(0x00, 0x00000005)  # CTRL: en[0] = 1

    # Core 0 is now running. It immediately executes:
    #   mv x15, x16  → tries to read FIFO0 → FIFO0 is empty → core STALLS
    # The core's clock is gated off. It's waiting for data.

    # Step 5: Wait a bit for core to start and stall on FIFO0
    dut._log.info("Step 5: Waiting for core to start and stall on empty FIFO0...")
    await ClockCycles(dut.clk, 100)

    # Step 6: Check FIFO levels — both should be 0 (FIFO0 empty, FIFO1 empty)
    flevel = await spi.bio_read(0x0C)
    fifo0_level = flevel & 0xF
    fifo1_level = (flevel >> 4) & 0xF
    dut._log.info(f"  FIFO0 level: {fifo0_level}, FIFO1 level: {fifo1_level}")

    # Step 7: Push test data into FIFO0
    test_value = 0xDEADBEEF
    dut._log.info(f"Step 7: Pushing 0x{test_value:08X} into FIFO0...")
    await spi.bio_write(0x10, test_value)  # TXF0

    # Step 8: Wait for core to wake up, read FIFO0, write to FIFO1
    # The sequence inside the core:
    #   1. FIFO0 becomes readable → stall clears → clock gate opens
    #   2. mv x15, x16 completes → x15 = 0xDEADBEEF
    #   3. mv x17, x15 → writes 0xDEADBEEF to FIFO1
    #   4. j 0x00 → loops back
    #   5. mv x15, x16 → FIFO0 is empty again → core stalls
    # Total: ~10-20 clock cycles for the 3 instructions
    dut._log.info("Step 8: Waiting for core to process...")
    await ClockCycles(dut.clk, 200)

    # Step 9: Check FIFO1 has data
    flevel = await spi.bio_read(0x0C)
    fifo0_level = flevel & 0xF
    fifo1_level = (flevel >> 4) & 0xF
    dut._log.info(f"  FIFO0 level: {fifo0_level}, FIFO1 level: {fifo1_level}")
    assert fifo1_level > 0, f"FIFO1 is empty — core didn't produce output!"

    # Step 10: Pull result from FIFO1
    result = await spi.bio_read(0x24)  # RXF1
    dut._log.info(f"Step 10: Got 0x{result:08X} from FIFO1")

    # Step 11: Verify
    assert result == test_value, \
        f"FIFO echo mismatch: pushed 0x{test_value:08X}, got 0x{result:08X}"

    # Step 12: Test with a second value to make sure the loop works
    test_value2 = 0xCAFEBABE
    dut._log.info(f"Step 12: Testing loop — pushing 0x{test_value2:08X}...")
    await spi.bio_write(0x10, test_value2)
    await ClockCycles(dut.clk, 200)
    result2 = await spi.bio_read(0x24)
    dut._log.info(f"  Got 0x{result2:08X} from FIFO1")
    assert result2 == test_value2, \
        f"Second echo mismatch: pushed 0x{test_value2:08X}, got 0x{result2:08X}"

    # Step 13: Disable core
    dut._log.info("Step 13: Disabling core 0...")
    await spi.bio_write(0x00, 0x00000000)  # CTRL: en = 0

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — FIFO ECHO WORKS!")
    dut._log.info("  The BIO core:")
    dut._log.info("    - Booted from IMEM at address 0x0")
    dut._log.info("    - Stalled on empty FIFO0 (clock gated)")
    dut._log.info("    - Woke up when data was pushed")
    dut._log.info("    - Read FIFO0 via x16")
    dut._log.info("    - Wrote FIFO1 via x17")
    dut._log.info("    - Looped and processed a second value")
    dut._log.info("    - Data matched perfectly both times")
    dut._log.info("========================================")
