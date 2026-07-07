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
async def test_aclk(dut):
    """bunnie's aclk_test: verify x31 clock counter and instruction cycle counts.
    
    Program pushes x31 (clock counter) to FIFO1 multiple times with
    different instruction patterns between reads. The differences between
    consecutive counter values reveal the exact cycle count per instruction.
    
    From units.rs lines 1053-1064."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== bunnie's ACLK test ===")

    # bunnie's aclk program (runs on core 1 in original, we use core 0):
    #
    #   mv x17, x31     → push clock counter to FIFO1
    #   mv x17, x31     → push again (measures mv+push cycle count)
    #   mv x17, x31     → push again
    #   nop              → 1 extra cycle
    #   mv x17, x31     → push again (measures nop cycle count)
    #   mv x17, x31     → push again
    #   mv x20, x0      → wait quantum
    #   mv x17, x31     → push again (measures quantum wait)
    #   mv x20, x0      → wait quantum
    #   mv x17, x31     → push again
    #
    # Encodings:
    #   mv x17, x31 = addi x17, x31, 0 = (31 << 15) | (17 << 7) | 0x13
    #   x31 = 11111 = 0x1F
    #   (0x1F << 15) = 0x000F8000
    #   (17 << 7)    = 0x00000880
    #   0x000F8000 | 0x00000880 | 0x13 = 0x000F8893
    #
    #   nop = 0x00000013
    #   mv x20, x0 = 0x00000A13

    program = [
        0x000F8893,  # [0x00] mv x17, x31     push clk_count to FIFO1
        0x000F8893,  # [0x04] mv x17, x31
        0x000F8893,  # [0x08] mv x17, x31
        0x00000013,  # [0x0C] nop
        0x000F8893,  # [0x10] mv x17, x31
        0x000F8893,  # [0x14] mv x17, x31
        0x00000A13,  # [0x18] mv x20, x0      wait quantum
        0x000F8893,  # [0x1C] mv x17, x31
        0x00000A13,  # [0x20] mv x20, x0      wait quantum
        0x000F8893,  # [0x24] mv x17, x31
    ]

    # Load into core 0
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Set quantum divider = 10 cycles (small, for fast test)
    QDIV = 10
    await spi.bio_write(0x50, QDIV << 16)

    # Enable core 0 with restart
    await spi.bio_write(0x00, 0x00000005)

    # Wait for FIFO1 to fill (7+ entries)
    await ClockCycles(dut.clk, 2000)

    # Check FIFO1 level
    flevel = await spi.bio_read(0x0C)
    fifo1_level = (flevel >> 4) & 0xF
    dut._log.info(f"FIFO1 level: {fifo1_level}")
    assert fifo1_level >= 7, f"Expected >=7 entries, got {fifo1_level}"

    # Read 7 clock counter values
    results = []
    for i in range(7):
        val = await spi.bio_read(0x24)  # RXF1
        val = val & 0x3FFFFFFF  # mask off core_id bits (upper 2 bits)
        results.append(val)
        dut._log.info(f"  [{i}]: {val} cycles")

    # Calculate deltas
    dut._log.info("")
    dut._log.info("Cycle deltas:")
    deltas = []
    for i in range(1, len(results)):
        delta = results[i] - results[i-1]
        deltas.append(delta)
        dut._log.info(f"  [{i-1}]→[{i}]: {delta} cycles")

    # Verify deltas (from bunnie's assertions, adjusted for REGISTER_RAM mode):
    # results[1] - results[0] == 3 (back-to-back mv x17, x31)
    # results[2] - results[1] == 3 or 4 (REGISTER_RAM variability)
    # results[3] - results[2] == 6 or 7 (nop + mv x17, x31)
    # results[4] - results[3] == 3 (back-to-back mv x17, x31)
    # results[6] - results[5] == 10 (quantum wait with QDIV=10)

    all_pass = True

    ok = deltas[0] == 3
    dut._log.info(f"  Delta 0 == 3: {deltas[0]} {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    ok = deltas[1] in (3, 4)
    dut._log.info(f"  Delta 1 == 3 or 4: {deltas[1]} {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    ok = deltas[2] in (6, 7)
    dut._log.info(f"  Delta 2 == 6 or 7: {deltas[2]} {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    ok = deltas[3] == 3
    dut._log.info(f"  Delta 3 == 3: {deltas[3]} {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    ok = deltas[5] == QDIV
    dut._log.info(f"  Delta 5 == {QDIV}: {deltas[5]} {'PASS' if ok else 'FAIL'}")
    all_pass &= ok

    await spi.bio_write(0x00, 0x00000000)

    assert all_pass, "Cycle count mismatch!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — ACLK test works!")
    dut._log.info("  x31 clock counter verified")
    dut._log.info("  Instruction cycle timing correct")
    dut._log.info("========================================")
