import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from spi_master import SPIMaster


@cocotb.test()
async def test_chip_id(dut):
    """Test 1: Read CHIP_ID register (0xFC) — proves SPI read path works."""

    # Start clock (25 MHz = 40ns period)
    cocotb.start_soon(Clock(dut.clk, 40, units="ns").start())

    # Initialize inputs
    dut.reset_n.value = 0
    dut.spi_cs_n.value = 1
    dut.spi_sclk.value = 0
    dut.spi_mosi.value = 0
    dut.gpio_in.value = 0

    # Hold reset for 10 cycles
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)

    # Create SPI master (1 MHz SPI clock = 1000ns period)
    spi = SPIMaster(dut, spi_period_ns=1000)

    # Read CHIP_ID
    dut._log.info("Reading CHIP_ID (0xFC)...")

    # First read — might get stale data due to SPI pipeline
    result1 = await spi.bio_read(0xFC)
    dut._log.info(f"First read:  0x{result1:08X}")

    # Second read — should get correct data
    result2 = await spi.bio_read(0xFC)
    dut._log.info(f"Second read: 0x{result2:08X}")

    # Check which read got the right value
    expected = 0x0B100002

    if result1 == expected:
        dut._log.info(f"PASS — first read returned correct CHIP_ID: 0x{result1:08X}")
    elif result2 == expected:
        dut._log.info(f"PASS — second read returned correct CHIP_ID: 0x{result2:08X}")
        dut._log.info("(First read was pipeline delay — this is expected)")
    else:
        # Dump raw bytes for debug
        dut._log.error(f"FAIL — Expected 0x{expected:08X}")
        dut._log.error(f"  First read:  0x{result1:08X}")
        dut._log.error(f"  Second read: 0x{result2:08X}")

        # Try a raw 6-byte transfer for detailed analysis
        dut._log.info("Raw byte dump:")
        tx = [0x03, 0xFC, 0x00, 0x00, 0x00, 0x00]
        rx = await spi.transfer(tx)
        dut._log.info(f"  TX: {[hex(b) for b in tx]}")
        dut._log.info(f"  RX: {[hex(b) for b in rx]}")
        assert False, f"CHIP_ID mismatch"


@cocotb.test()
async def test_reg_write_read(dut):
    """Test 2: Write QDIV0, read it back — proves register write+read path."""

    # Start clock
    cocotb.start_soon(Clock(dut.clk, 40, units="ns").start())

    # Reset
    dut.reset_n.value = 0
    dut.spi_cs_n.value = 1
    dut.spi_sclk.value = 0
    dut.spi_mosi.value = 0
    dut.gpio_in.value = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)

    spi = SPIMaster(dut, spi_period_ns=1000)

    # Write a known value to QDIV0
    test_val = 0xDEADBEEF
    dut._log.info(f"Writing 0x{test_val:08X} to QDIV0 (0x50)...")
    await spi.bio_write(0x50, test_val)

    # Read it back (double-read for pipeline)
    result = await spi.bio_read_double(0x50)
    dut._log.info(f"Read back: 0x{result:08X}")

    assert result == test_val, f"QDIV0 mismatch: wrote 0x{test_val:08X}, read 0x{result:08X}"
    dut._log.info("PASS — register write+read works")
