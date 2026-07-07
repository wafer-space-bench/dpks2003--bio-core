import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles
from spi_master import SPIMaster


# =============================================================================
# Reset helper
# =============================================================================

async def reset_dut(dut):
    """Common reset sequence."""
    cocotb.start_soon(Clock(dut.clk, 40, units="ns").start())
    dut.reset_n.value  = 0
    dut.spi_cs_n.value = 1
    dut.spi_sclk.value = 0
    dut.spi_mosi.value = 0
    dut.gpio_in.value  = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)


# =============================================================================
# UART capture helper
# =============================================================================

async def capture_uart_byte(dut, bit_period_cycles):
    """
    Robust 8N1 UART capture with self-calibrating centre-tap.
    
    Instead of blindly offsetting by bit_period//2 after edge detection
    (which has up to 1-bit-period of jitter), we measure the actual
    falling-edge clock cycle and compute the true mid-bit sample point.
    """

    # ------------------------------------------------------------------
    # 1. Wait for falling edge of start bit — record exact cycle count
    # ------------------------------------------------------------------
    timeout_cycles = bit_period_cycles * 30
    prev_gpio = 1
    found = False

    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        gpio = int(dut.gpio_out.value) & 1
        if prev_gpio == 1 and gpio == 0:
            found = True
            break
        prev_gpio = gpio

    if not found:
        return None

    # ------------------------------------------------------------------
    # 2. Wait for EXACTLY the middle of bit 0 (first data bit)
    #
    # We are now at the START of the start bit.
    # To reach mid-bit-0 we need:
    #   - 1 full start-bit period  (to skip the start bit)
    #   - + half a bit period      (to land in centre of bit 0)
    #
    # Total from falling edge: bit_period_cycles + bit_period_cycles//2
    # Then each subsequent bit is exactly bit_period_cycles later.
    # ------------------------------------------------------------------
    await ClockCycles(dut.clk, bit_period_cycles + bit_period_cycles // 2)

    # ------------------------------------------------------------------
    # 3. Sample 8 data bits
    # ------------------------------------------------------------------
    byte_val = 0
    for bit_idx in range(8):
        gpio = int(dut.gpio_out.value) & 1
        byte_val |= (gpio << bit_idx)
        if bit_idx < 7:
            await ClockCycles(dut.clk, bit_period_cycles)

    # ------------------------------------------------------------------
    # 4. Wait out the stop bit so the line is idle for the caller
    # ------------------------------------------------------------------
    await ClockCycles(dut.clk, bit_period_cycles + bit_period_cycles // 2)

    return byte_val

# =============================================================================
# Send + capture — THE KEY FIX
# =============================================================================

async def send_and_capture(dut, spi, byte_val, bit_period_cycles):
    """
    Start the capture coroutine BEFORE writing the byte to the FIFO so
    that the falling-edge detector is already armed when the BIO core
    begins transmitting.

    The previous code did:
        await spi.bio_write(...)          # BIO starts TX immediately
        rx = await capture_uart_byte(...) # capture misses the start bit ← BUG

    The fixed sequence is:
        capture_task = cocotb.start_soon(capture_uart_byte(...))
        await ClockCycles(dut.clk, SETTLE)   # let coroutine reach its first await
        await spi.bio_write(...)             # now trigger the transmitter
        rx = await capture_task              # collect result
    """
    # How many clocks to wait after launching the capture coroutine before
    # we trigger the transmitter.  Any small number ≥ 1 is fine; 10 gives
    # comfortable margin without meaningfully slowing the sim.
    SETTLE_CYCLES = 10

    # Arm the capture coroutine — it immediately parks at RisingEdge waiting
    # for the start bit falling edge.
    capture_task = cocotb.start_soon(capture_uart_byte(dut, bit_period_cycles))

    # Let the coroutine advance past its first await so it is truly listening.
    await ClockCycles(dut.clk, SETTLE_CYCLES)

    # Now push the byte into the FIFO — BIO core will dequeue and TX.
    await spi.bio_write(0x10, byte_val)

    # Collect the captured byte.
    rx_byte = await capture_task
    return rx_byte


# =============================================================================
# Main test
# =============================================================================

@cocotb.test()
async def test_uart_tx(dut):
    """UART TX bitbang: BIO core sends bytes 1-10 on GPIO[0]."""

    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== UART TX Bitbang Test ===")

    # =========================================================================
    # Hand-encoded UART TX program for BIO core 0
    #
    # Protocol: 8N1 (start bit, 8 data bits LSB first, stop bit)
    # GPIO[0] = TX pin, idle HIGH
    # Quantum = 1 bit period
    #
    # Program flow:
    #   0x00: li x1, 1             # mask = bit 0
    #   0x04: mv x26, x1           # set GPIO mask
    #   0x08: mv x24, x1           # set direction = output
    #   0x0C: mv x22, x1           # idle: set pin 0 HIGH
    #
    # loop:
    #   0x10: mv x15, x16          # read byte from FIFO0 (BLOCKS)
    #
    #   Start bit:
    #   0x14: mv x21, x0           # clear pin 0 (start bit = LOW)
    #   0x18: mv x20, x0           # wait 1 quantum
    #
    #   Bit 0:
    #   0x1C: mv x21, x15          # write bit 0 to GPIO (clear-on-0)
    #   0x20: mv x20, x0           # wait 1 quantum
    #   0x24: srli x15, x15, 1     # shift to next bit
    #
    #   Bits 1-6: same pattern ...
    #
    #   Bit 7:
    #   0x70: mv x21, x15
    #   0x74: mv x20, x0
    #
    #   Stop bit:
    #   0x78: mv x22, x1           # set pin 0 HIGH (stop bit)
    #   0x7C: mv x20, x0           # wait 1 quantum
    #
    #   0x80: j -112               # back to 0x10 (FIFO read)
    # =========================================================================

    uart_tx_program = [
        # Setup (run once)
        0x00100093,  # [0x00] li   x1,  1          mask = bit 0
        0x00008D13,  # [0x04] mv   x26, x1          GPIO mask
        0x00008C13,  # [0x08] mv   x24, x1          direction = output
        0x00008B13,  # [0x0C] mv   x22, x1          idle HIGH

        # Loop start: read byte from FIFO0
        0x00080793,  # [0x10] mv   x15, x16         read FIFO0 (BLOCKS)

        # Start bit
        0x00000A93,  # [0x14] mv   x21, x0          clear pin 0 (LOW)
        0x00000A13,  # [0x18] mv   x20, x0          wait quantum

        # Bit 0
        0x00078A93,  # [0x1C] mv   x21, x15         write bit to GPIO
        0x00000A13,  # [0x20] mv   x20, x0          wait quantum
        0x0017D793,  # [0x24] srli x15, x15, 1      shift right

        # Bit 1
        0x00078A93,  # [0x28] mv   x21, x15
        0x00000A13,  # [0x2C] mv   x20, x0
        0x0017D793,  # [0x30] srli x15, x15, 1

        # Bit 2
        0x00078A93,  # [0x34] mv   x21, x15
        0x00000A13,  # [0x38] mv   x20, x0
        0x0017D793,  # [0x3C] srli x15, x15, 1

        # Bit 3
        0x00078A93,  # [0x40] mv   x21, x15
        0x00000A13,  # [0x44] mv   x20, x0
        0x0017D793,  # [0x48] srli x15, x15, 1

        # Bit 4
        0x00078A93,  # [0x4C] mv   x21, x15
        0x00000A13,  # [0x50] mv   x20, x0
        0x0017D793,  # [0x54] srli x15, x15, 1

        # Bit 5
        0x00078A93,  # [0x58] mv   x21, x15
        0x00000A13,  # [0x5C] mv   x20, x0
        0x0017D793,  # [0x60] srli x15, x15, 1

        # Bit 6
        0x00078A93,  # [0x64] mv   x21, x15
        0x00000A13,  # [0x68] mv   x20, x0
        0x0017D793,  # [0x6C] srli x15, x15, 1

        # Bit 7 (last data bit — no shift needed)
        0x00078A93,  # [0x70] mv   x21, x15
        0x00000A13,  # [0x74] mv   x20, x0

        # Stop bit
        0x00008B13,  # [0x78] mv   x22, x1          set pin 0 HIGH
        0x00000A13,  # [0x7C] mv   x20, x0          wait quantum

        # Loop back to FIFO read
        0xF91FF06F,  # [0x80] j    -112             back to 0x10
    ]

    # -------------------------------------------------------------------------
    # Load program into core 0
    # -------------------------------------------------------------------------
    dut._log.info(f"Loading UART TX program ({len(uart_tx_program)} words)...")
    await spi.bio_write(0xE0, 0x00000000)          # select core 0 IMEM
    for addr, instr in enumerate(uart_tx_program):
        await spi.bio_imem_write(addr, instr)

    # -------------------------------------------------------------------------
    # Quantum divider
    #   QDIV = 200 clock cycles per quantum = 1 bit period
    #   At 25 MHz (40 ns/cycle): 200 × 40 ns = 8 µs → ~125 kbaud (sim speed)
    # -------------------------------------------------------------------------
    QDIV = 200
    dut._log.info(f"Setting QDIV0 = {QDIV} cycles per quantum...")
    await spi.bio_write(0x50, QDIV << 16)

    # -------------------------------------------------------------------------
    # Enable core 0
    # -------------------------------------------------------------------------
    dut._log.info("Enabling core 0...")
    await spi.bio_write(0x00, 0x00000005)

    # Give the core enough time to run the 4-instruction setup and stall on
    # the FIFO0 read (x15 ← x16).  A few hundred cycles is plenty.
    await ClockCycles(dut.clk, 500)

    # -------------------------------------------------------------------------
    # Verify UART idle state (GPIO[0] must be HIGH)
    # -------------------------------------------------------------------------
    gpio_idle = int(dut.gpio_out.value) & 1
    dut._log.info(f"GPIO[0] idle state: {gpio_idle} (expected 1)")
    assert gpio_idle == 1, "GPIO[0] should be HIGH (UART idle) before transmission"

    # -------------------------------------------------------------------------
    # Send bytes 1-10 and capture UART output
    #
    # KEY FIX: send_and_capture() arms the capture coroutine BEFORE writing
    # to the FIFO so we never miss the start bit falling edge.
    # -------------------------------------------------------------------------
    BYTES_TO_SEND = range(1, 11)
    received = []

    for byte_to_send in BYTES_TO_SEND:
        dut._log.info(f"Sending byte {byte_to_send}...")

        # Arm capture first, then trigger TX — see send_and_capture() above.
        rx_byte = await send_and_capture(dut, spi, byte_to_send, QDIV)

        if rx_byte is not None:
            match = "MATCH" if rx_byte == byte_to_send else "MISMATCH"
            dut._log.info(f"  Received: {rx_byte} {match}")
        else:
            dut._log.error("  Timeout waiting for UART byte!")

        received.append(rx_byte)

        # Wait for the line to return to idle before sending the next byte.
        # The stop bit is 1 quantum = QDIV cycles, already consumed inside
        # capture_uart_byte(), but we add a small guard margin.
        # We also poll for GPIO HIGH as a belt-and-braces idle check.
        await ClockCycles(dut.clk, QDIV * 2)     # guard margin

        idle_timeout = QDIV * 5
        for _ in range(idle_timeout):
            await RisingEdge(dut.clk)
            if (int(dut.gpio_out.value) & 1) == 1:
                break
        # (If we fall through the loop the line is still low — the next
        #  capture will detect that and the assert below will catch it.)

    # -------------------------------------------------------------------------
    # Results
    # -------------------------------------------------------------------------
    dut._log.info("")
    dut._log.info("Results:")
    all_pass = True
    for i, (sent, got) in enumerate(zip(BYTES_TO_SEND, received)):
        status = "PASS" if got == sent else "FAIL"
        if got != sent:
            all_pass = False
        dut._log.info(f"  Byte {i}: sent={sent}, received={got} — {status}")

    # Disable core
    await spi.bio_write(0x00, 0x00000000)

    assert all_pass, "Some UART bytes did not match!"

    dut._log.info("")
    dut._log.info("=" * 56)
    dut._log.info("  PASS — UART TX Bitbang works!")
    dut._log.info("  BIO core bit-banged 10 bytes on GPIO[0]")
    dut._log.info("  8N1 format, quantum-timed bit periods")
    dut._log.info("  All bytes verified correct")
    dut._log.info("=" * 56)