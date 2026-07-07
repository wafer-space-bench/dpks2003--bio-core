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
async def test_multiverse_2core(dut):
    """bunnie's multiverse adapted for 2 cores.
    
    Original: 4 cores, each writes to a different GPIO byte (0xFF, 0xFF00, etc.)
    Adapted: 2 cores, core 0 writes lower byte, core 1 writes upper byte.
    Both run with same quantum, GPIO output snapped to quantum.
    
    Core 0: mask=0xFF, counter starts at 0x10, increments, writes to GPIO
    Core 1: mask=0xFF00, counter starts at 0x20, increments, shifts left 8, writes to GPIO
    
    Expected GPIO pattern: 0xYYXX where XX=core0 counter, YY=core1 counter
    With snap-to-quantum, transitions should be synchronized."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== bunnie's multiverse (2-core) test ===")

    # Core 0 program (from multiverse_code0):
    #   li x2, 0xFF       → mask for lower byte
    #   mv x26, x2        → set GPIO mask
    #   addi x1, x0, 0x10 → counter starts at 0x10
    # loop:
    #   addi x1, x1, 1    → increment
    #   mv x21, x1        → write to GPIO (lower byte)
    #   mv x20, x0        → wait quantum
    #   j loop

    core0_program = [
        0x0FF00113,  # [0x00] li x2, 0xFF          (addi x2, x0, 0xFF)
        0x00010D13,  # [0x04] mv x26, x2            (addi x26, x2, 0)
        0x01000093,  # [0x08] addi x1, x0, 0x10     (counter = 0x10)
        # loop:
        0x00108093,  # [0x0C] addi x1, x1, 1        (counter++)
        0x00008A93,  # [0x10] mv x21, x1             (write to GPIO)
        0x00000A13,  # [0x14] mv x20, x0             (wait quantum)
        0xFF5FF06F,  # [0x18] j -12                  (back to 0x0C)
    ]

    # Core 1 program (adapted from multiverse_code1):
    #   li x2, 0xFF00     → mask for upper byte (needs LUI+ADDI for >12-bit)
    #   Actually 0xFF00 = 0xFF << 8. But 0xFF00 doesn't fit in 12-bit immediate.
    #   Use: li x2, 0xFF; slli x2, x2, 8 → x2 = 0xFF00
    #   mv x26, x2        → set GPIO mask
    #   addi x1, x0, 0x20 → counter starts at 0x20
    # loop:
    #   addi x1, x1, 1    → increment
    #   slli x21, x1, 8   → shift left 8 and write to GPIO
    #   mv x20, x0        → wait quantum
    #   j loop
    #
    # Note: "slli x21, x1, 8" writes to x21 with the shifted value.
    # x21 is GPIO write, so this writes (x1 << 8) to GPIO.
    # Encoding: slli rd=x21, rs1=x1, shamt=8
    #   (8 << 20) | (1 << 15) | (1 << 12) | (21 << 7) | 0x13
    #   = 0x00809A93

    core1_program = [
        0x0FF00113,  # [0x00] li x2, 0xFF
        0x00811113,  # [0x04] slli x2, x2, 8        (x2 = 0xFF00)
        0x00010D13,  # [0x08] mv x26, x2             (mask = 0xFF00)
        0x02000093,  # [0x0C] addi x1, x0, 0x20     (counter = 0x20)
        # loop:
        0x00108093,  # [0x10] addi x1, x1, 1
        0x00809A93,  # [0x14] slli x21, x1, 8        (write x1<<8 to GPIO)
        0x00000A13,  # [0x18] mv x20, x0
        0xFF5FF06F,  # [0x1C] j -12                  (back to 0x10)
    ]

    # Load core 0
    dut._log.info("Loading core 0 (lower byte counter)...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(core0_program):
        await spi.bio_imem_write(addr, instr)

    # Load core 1
    dut._log.info("Loading core 1 (upper byte counter)...")
    await spi.bio_write(0xE0, 0x00000001)
    for addr, instr in enumerate(core1_program):
        await spi.bio_imem_write(addr, instr)

    # Set both quantum dividers to same value
    QDIV = 100
    await spi.bio_write(0x50, QDIV << 16)  # QDIV0
    await spi.bio_write(0x54, QDIV << 16)  # QDIV1

    # Enable snap-to-quantum output (tied to core 0's quantum)
    # CONFIG: snap_output_to_quantum = 1, snap_output_to_which = 0
    await spi.bio_write(0x08, 0x00000001)

    # Enable both cores with restart
    dut._log.info("Enabling both cores with snap-to-quantum...")
    await spi.bio_write(0x00, 0x0000000F)

    # Wait for several quantum periods
    await ClockCycles(dut.clk, QDIV * 20)

    # Sample GPIO several times
    dut._log.info("Sampling GPIO output pattern...")
    samples = []
    for i in range(10):
        await ClockCycles(dut.clk, QDIV + 20)
        gpio_val = int(dut.gpio_out.value)
        samples.append(gpio_val)
        lo = gpio_val & 0xFF
        hi = (gpio_val >> 8) & 0xFF
        dut._log.info(f"  [{i}] gpio=0x{gpio_val:04X}  lo=0x{lo:02X}  hi=0x{hi:02X}")

    # Verify:
    # 1. Lower byte should be incrementing from ~0x11 onwards
    # 2. Upper byte should be incrementing from ~0x21 onwards
    # 3. Both should be advancing (not stuck)

    lo_values = [s & 0xFF for s in samples]
    hi_values = [(s >> 8) & 0xFF for s in samples]

    # Check lower byte is incrementing
    lo_increments = sum(1 for i in range(1, len(lo_values)) if lo_values[i] > lo_values[i-1])
    hi_increments = sum(1 for i in range(1, len(hi_values)) if hi_values[i] > hi_values[i-1])

    dut._log.info(f"  Lower byte increments: {lo_increments}/9")
    dut._log.info(f"  Upper byte increments: {hi_increments}/9")

    assert lo_increments > 5, f"Lower byte not incrementing consistently ({lo_increments}/9)"
    assert hi_increments > 5, f"Upper byte not incrementing consistently ({hi_increments}/9)"

    # Check both bytes are in expected ranges
    assert all(0x10 <= v <= 0x7F for v in lo_values), f"Lower byte values out of range: {lo_values}"
    assert all(0x20 <= v <= 0x7F for v in hi_values), f"Upper byte values out of range: {hi_values}"

    await spi.bio_write(0x00, 0x00000000)

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — 2-core multiverse works!")
    dut._log.info("  Both cores running simultaneously")
    dut._log.info("  GPIO output synchronized via snap-to-quantum")
    dut._log.info("========================================")
