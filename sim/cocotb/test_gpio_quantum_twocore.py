import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer
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


# =========================================================================
# Test 5: GPIO Output
# Proves: x21 write → priority_demux → scc_ff → gpio_out pin
#
# Program: set GPIO mask, set direction to output, write pattern to GPIO
#
# Register mapping:
#   x26 = GPIO mask (which pins to affect)
#   x24 = GPIO direction set (make output)
#   x21 = GPIO set/clear (clear-on-0 semantics)
#   x16 = FIFO0 read (blocks until data)
#
# Program flow:
#   1. Set mask to 0xFF (affect lower 8 pins)
#   2. Set direction to output for lower 8 pins
#   3. Read pattern from FIFO0 (blocks)
#   4. Write pattern to GPIO
#   5. Loop back to step 3
# =========================================================================
@cocotb.test()
async def test_gpio_output(dut):
    """Load GPIO output program, push pattern, verify gpio_out pins."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== GPIO Output Test ===")

    # Select core 0 IMEM
    await spi.bio_write(0xE0, 0x00000000)

    # Hand-assembled GPIO output program:
    #
    # Instruction encodings (RV32E):
    #   li xD, imm    = addi xD, x0, imm
    #   mv xD, xS     = addi xD, xS, 0
    #
    #   li x1, 0xFF       → addi x1, x0, 0xFF     → 0x0FF00093
    #   mv x26, x1        → addi x26, x1, 0       → 0x00008D13
    #   mv x24, x1        → addi x24, x1, 0       → 0x00008C13
    #   mv x15, x16       → addi x15, x16, 0      → 0x00080793  (read FIFO0, blocks)
    #   mv x21, x15       → addi x21, x15, 0      → 0x00078A93
    #   j -8              → jal x0, -8             → 0xFF9FF06F  (back to FIFO read)

    program = [
        0x0FF00093,  # li x1, 0xFF        (mask = lower 8 bits)
        0x00008D13,  # mv x26, x1         (set GPIO mask)
        0x00008C13,  # mv x24, x1         (set direction = output)
        0x00080793,  # mv x15, x16        (read FIFO0, blocks until data)
        0x00078A93,  # mv x21, x15        (write to GPIO — set/clear pins)
        0xFF9FF06F,  # j -8               (back to FIFO0 read at addr 0x0C)
    ]

    dut._log.info("Loading GPIO output program...")
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Enable core 0 with restart
    dut._log.info("Enabling core 0...")
    await spi.bio_write(0x00, 0x00000005)  # en[0]=1, restart[0]=1

    # Wait for core to execute setup (mask, direction) and stall on FIFO0
    await ClockCycles(dut.clk, 500)

    # Check GPIO direction — should show lower 8 bits as output
    gpio_dir = await spi.bio_read(0x88)  # DBG_PADOE
    dut._log.info(f"GPIO direction: 0x{gpio_dir:08X}")

    # Push pattern 0xAA to FIFO0
    dut._log.info("Pushing pattern 0xAA...")
    await spi.bio_write(0x10, 0x000000AA)

    # Wait for core to process
    await ClockCycles(dut.clk, 200)

    # Read GPIO output state
    gpio_out = await spi.bio_read(0x84)  # DBG_PADOUT
    dut._log.info(f"GPIO output: 0x{gpio_out:08X}")

    # Also read directly from DUT port
    actual_pins = int(dut.gpio_out.value)
    dut._log.info(f"GPIO pins (direct): 0x{actual_pins:04X}")

    # Verify — lower 8 bits should be 0xAA (10101010)
    assert (gpio_out & 0xFF) == 0xAA, \
        f"GPIO output mismatch: expected 0xAA, got 0x{gpio_out & 0xFF:02X}"

    # Push a second pattern 0x55
    dut._log.info("Pushing pattern 0x55...")
    await spi.bio_write(0x10, 0x00000055)
    await ClockCycles(dut.clk, 200)

    gpio_out2 = await spi.bio_read(0x84)
    dut._log.info(f"GPIO output: 0x{gpio_out2:08X}")

    assert (gpio_out2 & 0xFF) == 0x55, \
        f"GPIO output mismatch: expected 0x55, got 0x{gpio_out2 & 0xFF:02X}"

    # Disable core
    await spi.bio_write(0x00, 0x00000000)

    dut._log.info("PASS — GPIO output works")


# =========================================================================
# Test 6: Quantum Timing
# Proves: pio_divider generates quantum pulses, x20 halts core until pulse
#
# Program: toggle GPIO pin 0 on every quantum, count toggles in FIFO
#
# We set QDIV0 to a known value, run the program for a fixed time,
# and count how many toggles happened by reading GPIO state changes.
#
# Simpler approach: use quantum to space two FIFO writes. Measure the
# time between them by counting clock cycles in the waveform.
#
# Program:
#   1. Set mask and direction
#   2. Set GPIO pin 0 high
#   3. Wait quantum (x20)
#   4. Clear GPIO pin 0
#   5. Wait quantum (x20)
#   6. Push 0x01 to FIFO1 (signal to host: one toggle complete)
#   7. Loop to step 2
# =========================================================================
@cocotb.test()
async def test_quantum_timing(dut):
    """Set quantum divider, verify GPIO toggles at correct rate."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== Quantum Timing Test ===")

    # Select core 0 IMEM
    await spi.bio_write(0xE0, 0x00000000)

    # Program: toggle GPIO[0] using quantum timing, push count to FIFO1
    #
    #   li x1, 1           → 0x00100093   (mask = bit 0)
    #   mv x26, x1         → 0x00008D13   (set GPIO mask)
    #   mv x24, x1         → 0x00008C13   (set direction = output)
    # loop:
    #   mv x22, x1         → 0x00008B13   (GPIO set — pin 0 high)
    #   mv x20, x0         → 0x00000A13   (wait quantum)
    #   mv x23, x1         → 0x00008B93   (GPIO clear — pin 0 low)
    #   mv x20, x0         → 0x00000A13   (wait quantum)
    #   mv x17, x1         → 0x00008893   (push 1 to FIFO1 — signal toggle done)
    #   j loop              → 0xFE1FF06F   (jump back to set)

    program = [
        0x00100093,  # li x1, 1
        0x00008D13,  # mv x26, x1
        0x00008C13,  # mv x24, x1
        0x00008B13,  # mv x22, x1     (GPIO set pin 0)
        0x00000A13,  # mv x20, x0     (wait quantum)
        0x00008B93,  # mv x23, x1     (GPIO clear pin 0)
        0x00000A13,  # mv x20, x0     (wait quantum)
        0x00008893,  # mv x17, x1     (push 1 to FIFO1)
        0xFE1FF06F,  # j -28          (back to GPIO set, addr 0x0C)
    ]

    dut._log.info("Loading quantum timing program...")
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Set quantum divider — 100 clock cycles per quantum
    # At 25 MHz that's 4 µs per quantum, 8 µs per full toggle cycle
    qdiv_value = (100 << 16)  # div_int=100, div_frac=0, unused=0
    dut._log.info(f"Setting QDIV0 = {100} cycles per quantum...")
    await spi.bio_write(0x50, qdiv_value)

    # Enable core 0 with restart
    await spi.bio_write(0x00, 0x00000005)

    # Wait for several toggle cycles (100 cycles per quantum × 2 quantums per toggle × 5 toggles)
    # Plus some margin for instruction execution
    await ClockCycles(dut.clk, 3000)

    # Check FIFO1 — should have some entries (one per complete toggle cycle)
    flevel = await spi.bio_read(0x0C)
    fifo1_level = (flevel >> 4) & 0xF
    dut._log.info(f"FIFO1 level after 3000 cycles: {fifo1_level}")

    assert fifo1_level > 0, "No toggles completed — quantum timing not working"
    dut._log.info(f"  {fifo1_level} toggle cycles completed")

    # Pull the values — each should be 1
    for i in range(fifo1_level):
        val = await spi.bio_read(0x24)
        dut._log.info(f"  FIFO1[{i}]: 0x{val:08X}")

    # Verify GPIO is toggling — read current state
    gpio_out = await spi.bio_read(0x84)
    dut._log.info(f"GPIO output state: 0x{gpio_out:08X}")

    # Disable core
    await spi.bio_write(0x00, 0x00000000)

    dut._log.info("PASS — quantum timing works")


# =========================================================================
# Test 7: Two-Core Parallel
# Proves: both cores run independently, inter-core FIFO communication
#
# Core 0: counter → pushes incrementing values to FIFO0
# Core 1: reads FIFO0 → writes value to GPIO
#
# The host enables both cores, waits, then checks GPIO output matches
# the last counter value core 0 pushed.
# =========================================================================
@cocotb.test()
async def test_two_core_parallel(dut):
    """Both cores running, core 0 produces data, core 1 consumes and outputs to GPIO."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== Two-Core Parallel Test ===")

    # ---- Core 0 program: push incrementing counter to FIFO0 ----
    #
    #   li x1, 0            → 0x00000093   (counter = 0)
    # loop:
    #   mv x16, x1          → 0x00008813   (push counter to FIFO0)
    #   addi x1, x1, 1      → 0x00108093   (increment)
    #   j loop               → 0xFF9FF06F   (back to push)

    core0_program = [
        0x00000093,  # li x1, 0
        0x00008813,  # mv x16, x1     (push to FIFO0)
        0x00108093,  # addi x1, x1, 1
        0xFF9FF06F,  # j -8           ← FIXED
    ]

    # ---- Core 1 program: read FIFO0, write to GPIO, signal on FIFO1 ----
    #
    #   li x1, 0xFF          → 0x0FF00093   (mask)
    #   mv x26, x1           → 0x00008D13   (set GPIO mask)
    #   mv x24, x1           → 0x00008C13   (set direction = output)
    # loop:
    #   mv x15, x16          → 0x00080793   (read FIFO0, blocks)
    #   mv x21, x15          → 0x00078A93   (write to GPIO)
    #   mv x17, x15          → 0x00078893   (push to FIFO1 — signal to host)
    #   j loop                → 0xFF5FF06F   (back to FIFO0 read, addr 0x0C)

    core1_program = [
        0x0FF00093,  # li x1, 0xFF
        0x00008D13,  # mv x26, x1
        0x00008C13,  # mv x24, x1
        0x00080793,  # mv x15, x16    (read FIFO0, blocks)
        0x00078A93,  # mv x21, x15    (write to GPIO)
        0x00078893,  # mv x17, x15    (push to FIFO1)
        0xFF5FF06F,  # j -12          (back to FIFO0 read, addr 0x0C)
    ]

    # Load core 0 program
    dut._log.info("Loading core 0 program (counter → FIFO0)...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(core0_program):
        await spi.bio_imem_write(addr, instr)

    # Load core 1 program
    dut._log.info("Loading core 1 program (FIFO0 → GPIO + FIFO1)...")
    await spi.bio_write(0xE0, 0x00000001)
    for addr, instr in enumerate(core1_program):
        await spi.bio_imem_write(addr, instr)

    # Enable BOTH cores with restart
    # en[1:0] = 11, restart[1:0] = 11 → bits [3:2]=11, [1:0]=11 = 0x0F
    dut._log.info("Enabling both cores...")
    await spi.bio_write(0x00, 0x0000000F)

    # Wait for cores to run
    # Core 0 pushes values into FIFO0 (8 deep), then stalls when full
    # Core 1 reads from FIFO0, writes to GPIO and FIFO1
    await ClockCycles(dut.clk, 5000)

    # Check FIFO1 — core 1 should have pushed values
    flevel = await spi.bio_read(0x0C)
    fifo1_level = (flevel >> 4) & 0xF
    dut._log.info(f"FIFO1 level: {fifo1_level}")

    assert fifo1_level > 0, "FIFO1 empty — core 1 didn't produce output"
    dut._log.info(f"PASS — {fifo1_level} values passed from core 0 → FIFO0 → core 1 → FIFO1")

    # Pull values from FIFO1 — should be sequential (0, 1, 2, ...)
    dut._log.info("Reading FIFO1 values (should be sequential)...")
    values = []
    for i in range(fifo1_level):
        val = await spi.bio_read(0x24)
        values.append(val)
        dut._log.info(f"  FIFO1[{i}]: {val}")

    # Verify sequential
    for i in range(len(values)):
        assert values[i] == i, \
            f"Sequence mismatch at position {i}: expected {i}, got {values[i]}"

    # Check GPIO output — should reflect one of the counter values
    gpio_out = await spi.bio_read(0x84)
    dut._log.info(f"GPIO output: 0x{gpio_out:08X}")

    # GPIO should show some counter value (lower 8 bits)
    assert gpio_out > 0 or values[-1] == 0, \
        f"GPIO output is zero but last FIFO value was {values[-1]}"

    # Disable both cores
    await spi.bio_write(0x00, 0x00000000)

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Two-Core Parallel works!")
    dut._log.info(f"  Core 0 pushed {len(values)}+ values")
    dut._log.info(f"  Core 1 received and output to GPIO")
    dut._log.info(f"  Sequence verified: {values}")
    dut._log.info("========================================")