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
async def test_fifo_level_events(dut):
    """FIFO level event test — adapted from bunnie's fifo_level_tests.
    
    Tests that hardware events fire when FIFO levels match configured
    thresholds using equal, less-than, and greater-than comparisons.
    
    This is a host-only test — no cores running. Push/pop FIFO0 via SPI
    and check EVENT_STATUS[31:24] for hardware event bits.
    
    FIFO0 uses event levels [0] and [1] → event bits 24 and 25."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== FIFO Level Events Test ===")

    # Disable all cores — host-only test
    await spi.bio_write(0x00, 0x00000000)

    # Clear all FIFOs
    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # ====== TEST 1: Equal trigger ======
    dut._log.info("Test 1: Equal trigger (level == 3)")

    # Set FIFO0 event level 0 to threshold=3
    # ELEVEL register (0x30): 8 x 4-bit fields
    # level[0] = lowest 4 bits
    await spi.bio_write(0x30, 0x00000003)  # level[0] = 3

    # Set ETYPE: equal mask for event 0 (bit 0 of eq_mask)
    # ETYPE = {gt_mask[7:0], eq_mask[7:0], lt_mask[7:0]}
    # eq_mask bit 0 = 1, others = 0
    await spi.bio_write(0x34, 0x00000100)  # eq_mask = 0x01, lt_mask = 0x00, gt_mask = 0x00
    await ClockCycles(dut.clk, 10)

    # Push items one at a time and check event status
    all_pass = True
    for level in range(6):
        if level > 0:
            await spi.bio_write(0x10, 0xDEAD0000 + level)  # push to FIFO0
            await ClockCycles(dut.clk, 10)

        events = await spi.bio_read(0x40)  # EVENT_STATUS
        hw_events = (events >> 24) & 0xFF
        event_bit = hw_events & 1  # event level 0 → bit 24 → hw_events bit 0

        expected = 1 if level == 3 else 0
        ok = event_bit == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Level {level}: event={event_bit} (expected {expected}) — {status}")
        if not ok:
            all_pass = False

    # Drain FIFO
    for _ in range(5):
        await spi.bio_read(0x20)
    await spi.bio_write(0x48, 0x0F)  # clear all FIFOs
    await ClockCycles(dut.clk, 10)

    # ====== TEST 2: Less-than trigger ======
    dut._log.info("Test 2: Less-than trigger (level < 4)")

    await spi.bio_write(0x30, 0x00000004)  # level[0] = 4
    await spi.bio_write(0x34, 0x00000001)  # lt_mask = 0x01, eq_mask = 0, gt_mask = 0
    await ClockCycles(dut.clk, 10)

    for level in range(6):
        if level > 0:
            await spi.bio_write(0x10, 0xBEEF0000 + level)
            await ClockCycles(dut.clk, 10)

        events = await spi.bio_read(0x40)
        hw_events = (events >> 24) & 0xFF
        event_bit = hw_events & 1

        expected = 1 if level < 4 else 0
        ok = event_bit == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Level {level}: event={event_bit} (expected {expected}) — {status}")
        if not ok:
            all_pass = False

    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # ====== TEST 3: Greater-than trigger ======
    dut._log.info("Test 3: Greater-than trigger (level > 2)")

    await spi.bio_write(0x30, 0x00000002)  # level[0] = 2
    await spi.bio_write(0x34, 0x00010000)  # gt_mask = 0x01, eq_mask = 0, lt_mask = 0
    await ClockCycles(dut.clk, 10)

    for level in range(6):
        if level > 0:
            await spi.bio_write(0x10, 0xCAFE0000 + level)
            await ClockCycles(dut.clk, 10)

        events = await spi.bio_read(0x40)
        hw_events = (events >> 24) & 0xFF
        event_bit = hw_events & 1

        expected = 1 if level > 2 else 0
        ok = event_bit == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Level {level}: event={event_bit} (expected {expected}) — {status}")
        if not ok:
            all_pass = False

    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # ====== TEST 4: Less-than-or-equal (lt + eq combined) ======
    dut._log.info("Test 4: Less-than-or-equal trigger (level <= 3)")

    await spi.bio_write(0x30, 0x00000003)  # level[0] = 3
    await spi.bio_write(0x34, 0x00000101)  # eq_mask = 0x01, lt_mask = 0x01
    await ClockCycles(dut.clk, 10)

    for level in range(6):
        if level > 0:
            await spi.bio_write(0x10, 0xF00D0000 + level)
            await ClockCycles(dut.clk, 10)

        events = await spi.bio_read(0x40)
        hw_events = (events >> 24) & 0xFF
        event_bit = hw_events & 1

        expected = 1 if level <= 3 else 0
        ok = event_bit == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Level {level}: event={event_bit} (expected {expected}) — {status}")
        if not ok:
            all_pass = False

    await spi.bio_write(0x48, 0x0F)
    await ClockCycles(dut.clk, 10)

    # ====== TEST 5: Drain test — events during drain ======
    dut._log.info("Test 5: Events during drain (eq at level 2)")

    await spi.bio_write(0x30, 0x00000002)  # level[0] = 2
    await spi.bio_write(0x34, 0x00000100)  # eq_mask = 0x01
    await ClockCycles(dut.clk, 10)

    # Fill to 5
    for i in range(5):
        await spi.bio_write(0x10, 0xABCD0000 + i)
    await ClockCycles(dut.clk, 10)

    # Drain and check events
    for drain_level in range(5, -1, -1):
        events = await spi.bio_read(0x40)
        hw_events = (events >> 24) & 0xFF
        event_bit = hw_events & 1

        expected = 1 if drain_level == 2 else 0
        ok = event_bit == expected
        status = "PASS" if ok else "FAIL"
        dut._log.info(f"  Drain level {drain_level}: event={event_bit} (expected {expected}) — {status}")
        if not ok:
            all_pass = False

        if drain_level > 0:
            await spi.bio_read(0x20)  # pop FIFO0
            await ClockCycles(dut.clk, 10)

    await spi.bio_write(0x48, 0x0F)

    assert all_pass, "Some FIFO level event checks failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — FIFO level events work!")
    dut._log.info("  Equal trigger verified")
    dut._log.info("  Less-than trigger verified")
    dut._log.info("  Greater-than trigger verified")
    dut._log.info("  Combined lt+eq trigger verified")
    dut._log.info("  Drain events verified")
    dut._log.info("========================================")


@cocotb.test()
async def test_software_events(dut):
    """Test software event set/clear via host registers.
    
    Tests EVENT_SET (0x38), EVENT_CLR (0x3C), EVENT_STATUS (0x40).
    These are the software event bits [23:0] in aggregated_events."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== Software Events Test ===")

    # Disable all cores
    await spi.bio_write(0x00, 0x00000000)

    all_pass = True

    # Test 1: Set single event bit
    dut._log.info("Test 1: Set event bit 0")
    await spi.bio_write(0x3C, 0x00FFFFFF)  # clear all events first
    await ClockCycles(dut.clk, 10)
    await spi.bio_write(0x38, 0x00000001)  # set event bit 0
    await ClockCycles(dut.clk, 10)
    events = await spi.bio_read(0x40)
    ok = (events & 0x00FFFFFF) == 0x00000001
    dut._log.info(f"  Events: 0x{events & 0xFFFFFF:06X} (expected 0x000001) — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    # Test 2: Set multiple bits
    dut._log.info("Test 2: Set bits 0, 4, 8")
    await spi.bio_write(0x38, 0x00000110)  # set bits 4 and 8
    await ClockCycles(dut.clk, 10)
    events = await spi.bio_read(0x40)
    ok = (events & 0x00FFFFFF) == 0x00000111
    dut._log.info(f"  Events: 0x{events & 0xFFFFFF:06X} (expected 0x000111) — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    # Test 3: Clear specific bit
    dut._log.info("Test 3: Clear bit 4")
    await spi.bio_write(0x3C, 0x00000010)  # clear bit 4
    await ClockCycles(dut.clk, 10)
    events = await spi.bio_read(0x40)
    ok = (events & 0x00FFFFFF) == 0x00000101
    dut._log.info(f"  Events: 0x{events & 0xFFFFFF:06X} (expected 0x000101) — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    # Test 4: Clear all
    dut._log.info("Test 4: Clear all events")
    await spi.bio_write(0x3C, 0x00FFFFFF)
    await ClockCycles(dut.clk, 10)
    events = await spi.bio_read(0x40)
    ok = (events & 0x00FFFFFF) == 0x00000000
    dut._log.info(f"  Events: 0x{events & 0xFFFFFF:06X} (expected 0x000000) — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    # Test 5: Set pattern and verify
    dut._log.info("Test 5: Set pattern 0xABCDEF")
    await spi.bio_write(0x38, 0x00ABCDEF)
    await ClockCycles(dut.clk, 10)
    events = await spi.bio_read(0x40)
    ok = (events & 0x00FFFFFF) == 0x00ABCDEF
    dut._log.info(f"  Events: 0x{events & 0xFFFFFF:06X} (expected 0xABCDEF) — {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    assert all_pass, "Software event checks failed!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Software events work!")
    dut._log.info("========================================")
