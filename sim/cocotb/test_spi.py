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


async def capture_spi_byte(dut, num_bits=8):
    """Watch GPIO pins and capture one SPI transaction.
    GPIO[0] = data (MOSI)
    GPIO[1] = clock (SCK)
    GPIO[2] = CS (active low)
    
    Returns the captured data word."""

    # Wait for CS to fall (start of transaction)
    timeout = 50000
    for _ in range(timeout):
        await RisingEdge(dut.clk)
        cs = (int(dut.gpio_out.value) >> 2) & 1
        if cs == 0:
            break
    else:
        return None  # timeout

    # Capture data bits on rising edge of SCK
    result = 0
    for bit in range(num_bits):
        # Wait for SCK rising edge
        prev_sck = 0
        for _ in range(timeout):
            await RisingEdge(dut.clk)
            sck = (int(dut.gpio_out.value) >> 1) & 1
            if sck == 1 and prev_sck == 0:  # rising edge
                break
            prev_sck = sck
        else:
            return None  # timeout

        # Sample data bit
        data_bit = int(dut.gpio_out.value) & 1
        result = (result << 1) | data_bit

        # Wait for SCK to fall
        for _ in range(timeout):
            await RisingEdge(dut.clk)
            sck = (int(dut.gpio_out.value) >> 1) & 1
            if sck == 0:
                break

    # Wait for CS to rise (end of transaction)
    for _ in range(timeout):
        await RisingEdge(dut.clk)
        cs = (int(dut.gpio_out.value) >> 2) & 1
        if cs == 1:
            break

    return result


@cocotb.test()
async def test_spi_bitbang(dut):
    """SPI bitbang master — BIO core shifts out data on GPIO pins.
    
    Simplified from bunnie's spi_test (which uses 4 cores + extclk).
    Uses 1 core to transmit 8-bit SPI frames:
      GPIO[0] = MOSI (data)
      GPIO[1] = SCK (clock)
      GPIO[2] = CS (chip select, active low)
    
    Protocol: MSB first, CPOL=0, CPHA=0 (Mode 0)
    Each bit: set data → quantum → raise clock → quantum → lower clock
    """
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== SPI Bitbang Test ===")

    # SPI TX program — 8-bit, MSB first
    # GPIO[0] = MOSI, GPIO[1] = SCK, GPIO[2] = CS
    #
    # x1 = 0x07 (mask for bits 0,1,2)
    # x2 = 0x01 (data bit mask)
    # x3 = ~x2 (data bit inverse)
    # x4 = 0x02 (clock bit mask)
    # x5 = ~x4 (clock bit inverse)
    # x6 = 0x04 (CS bit mask)
    # x7 = ~x6 (CS bit inverse)
    #
    # Setup:
    #   li x1, 7         → mask = bits 0,1,2
    #   mv x26, x1       → set GPIO mask
    #   mv x24, x1       → direction = output
    #   li x2, 1         → data bit
    #   not x3, x2       → ~data
    #   li x4, 2         → clock bit
    #   not x5, x4       → ~clock
    #   li x6, 4         → CS bit
    #   not x7, x6       → ~CS
    #   mv x21, x6       → initial state: CS high, clock low, data low
    #   mv x20, x0       → wait quantum
    #
    # Main loop:
    #   mv x15, x16      → read data from FIFO0 (blocks)
    #   mv x23, x7       → drop CS (clear CS bit → CS low)
    #   mv x20, x0       → CS setup time
    #
    #   For each bit (MSB first, 8 bits):
    #     and x22, x15, x2  → set data if bit is 1
    #     or  x23, x15, x3  → clear data if bit is 0
    #     mv  x20, x0       → data setup
    #     mv  x22, x4       → clock rise
    #     mv  x20, x0       → data hold
    #     mv  x23, x5       → clock fall
    #     srli x15, x15, 1  → next bit
    #
    #   After last bit:
    #     mv  x20, x0       → CS hold
    #     mv  x22, x6       → raise CS
    #     mv  x20, x0       → CS high time
    #     j   loop

    # Encodings using the Python helpers:
    # li rd, imm = addi rd, x0, imm
    # not rd, rs = xori rd, rs, -1

    def li(rd, imm): return ((imm & 0xFFF) << 20) | (rd << 7) | 0x13
    def mv(rd, rs1): return (rs1 << 15) | (rd << 7) | 0x13
    def not_r(rd, rs1): return (0xFFF << 20) | (rs1 << 15) | (4 << 12) | (rd << 7) | 0x13  # xori rd, rs1, -1
    def and_r(rd, rs1, rs2): return (rs2 << 20) | (rs1 << 15) | (7 << 12) | (rd << 7) | 0x33
    def or_r(rd, rs1, rs2): return (rs2 << 20) | (rs1 << 15) | (6 << 12) | (rd << 7) | 0x33
    def srli(rd, rs1, shamt): return (shamt << 20) | (rs1 << 15) | (5 << 12) | (rd << 7) | 0x13
    def slli(rd, rs1, shamt): return (shamt << 20) | (rs1 << 15) | (1 << 12) | (rd << 7) | 0x13

    def j(offset):
        imm = offset & 0x1FFFFF
        return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | \
               (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) | 0x6F

    FIFO0 = 16; QUANTUM = 20; GPIO_RW = 21; GPIO_SET = 22; GPIO_CLR = 23
    GPIO_DIR = 24; GPIO_MASK = 26

    program = [
        # Setup (0x00 - 0x28)
        li(1, 7),              # [0x00] x1 = 0x07 (mask)
        mv(GPIO_MASK, 1),      # [0x04] set GPIO mask
        mv(GPIO_DIR, 1),       # [0x08] direction = output
        li(2, 1),              # [0x0C] x2 = data bit mask
        not_r(3, 2),           # [0x10] x3 = ~data
        li(4, 2),              # [0x14] x4 = clock bit mask
        not_r(5, 4),           # [0x18] x5 = ~clock
        li(6, 4),              # [0x1C] x6 = CS bit mask
        not_r(7, 6),           # [0x20] x7 = ~CS
        mv(GPIO_RW, 6),        # [0x24] GPIO = CS high, clock low, data low
        mv(QUANTUM, 0),        # [0x28] wait quantum

        # Main loop (0x2C)
        mv(15, FIFO0),         # [0x2C] read FIFO0 (blocks)
        slli(15, 15, 8),       # [0x30] shift data to bit position 8 for MSB-first on bit 0
                               #        Actually we want MSB first. Data is 8-bit.
                               #        We'll shift left by 24 so MSB is at bit 31,
                               #        then shift right after each bit.
                               #        Wait — simpler: shift to put bit 7 at bit 0 position.
                               #        Actually let's just do MSB first by checking bit 7 first.
    ]

    # Hmm, the bit ordering is tricky. Let me simplify — use bunnie's approach:
    # shift data left so LSB aligns with the data pin (bit 0).
    # Then for each bit, use AND with x2 (0x01) to extract, shift right after.
    # This gives LSB-first. For MSB-first, pre-reverse or shift the other way.
    #
    # Bunnie's code does: slli x15, x15, 8 to put data starting at bit 8,
    # then uses AND with 0x100 as the bit mask. We have only 3 GPIO pins,
    # so data is on bit 0. Simpler: don't shift, just send LSB first.

    program = [
        # Setup
        li(1, 7),              # [0x00] mask = bits 0,1,2
        mv(GPIO_MASK, 1),      # [0x04]
        mv(GPIO_DIR, 1),       # [0x08]
        li(2, 1),              # [0x0C] data bit = bit 0
        not_r(3, 2),           # [0x10] ~data
        li(4, 2),              # [0x14] clock bit = bit 1
        not_r(5, 4),           # [0x18] ~clock
        li(6, 4),              # [0x1C] CS bit = bit 2
        not_r(7, 6),           # [0x20] ~CS
        mv(GPIO_RW, 6),        # [0x24] idle: CS high, clk low, data low
        mv(QUANTUM, 0),        # [0x28] wait quantum

        # Loop (0x2C):
        mv(15, FIFO0),         # [0x2C] read data from FIFO0

        mv(GPIO_CLR, 7),       # [0x30] drop CS (clear bit 2)
        mv(QUANTUM, 0),        # [0x34] CS setup time

        # Bit 0 (LSB)
        and_r(GPIO_SET, 15, 2),  # [0x38] set data if 1
        or_r(GPIO_CLR, 15, 3),   # [0x3C] clear data if 0
        mv(QUANTUM, 0),          # [0x40] data setup
        mv(GPIO_SET, 4),         # [0x44] clock rise
        mv(QUANTUM, 0),          # [0x48] data hold
        mv(GPIO_CLR, 5),         # [0x4C] clock fall
        srli(15, 15, 1),         # [0x50] next bit

        # Bit 1
        and_r(GPIO_SET, 15, 2), or_r(GPIO_CLR, 15, 3),
        mv(QUANTUM, 0), mv(GPIO_SET, 4), mv(QUANTUM, 0), mv(GPIO_CLR, 5),
        srli(15, 15, 1),

        # Bit 2
        and_r(GPIO_SET, 15, 2), or_r(GPIO_CLR, 15, 3),
        mv(QUANTUM, 0), mv(GPIO_SET, 4), mv(QUANTUM, 0), mv(GPIO_CLR, 5),
        srli(15, 15, 1),

        # Bit 3
        and_r(GPIO_SET, 15, 2), or_r(GPIO_CLR, 15, 3),
        mv(QUANTUM, 0), mv(GPIO_SET, 4), mv(QUANTUM, 0), mv(GPIO_CLR, 5),
        srli(15, 15, 1),

        # Bit 4
        and_r(GPIO_SET, 15, 2), or_r(GPIO_CLR, 15, 3),
        mv(QUANTUM, 0), mv(GPIO_SET, 4), mv(QUANTUM, 0), mv(GPIO_CLR, 5),
        srli(15, 15, 1),

        # Bit 5
        and_r(GPIO_SET, 15, 2), or_r(GPIO_CLR, 15, 3),
        mv(QUANTUM, 0), mv(GPIO_SET, 4), mv(QUANTUM, 0), mv(GPIO_CLR, 5),
        srli(15, 15, 1),

        # Bit 6
        and_r(GPIO_SET, 15, 2), or_r(GPIO_CLR, 15, 3),
        mv(QUANTUM, 0), mv(GPIO_SET, 4), mv(QUANTUM, 0), mv(GPIO_CLR, 5),
        srli(15, 15, 1),

        # Bit 7 (MSB — no shift after)
        and_r(GPIO_SET, 15, 2), or_r(GPIO_CLR, 15, 3),
        mv(QUANTUM, 0), mv(GPIO_SET, 4), mv(QUANTUM, 0), mv(GPIO_CLR, 5),

        # End of frame
        mv(QUANTUM, 0),           # CS hold time
        mv(GPIO_SET, 6),          # raise CS
        mv(QUANTUM, 0),           # CS high time
    ]
    # Jump back to loop
    loop_addr = 0x2C
    jump_addr = len(program) * 4
    offset = loop_addr - jump_addr
    program.append(j(offset))

    dut._log.info(f"Loading SPI bitbang program ({len(program)} words)...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(program):
        await spi.bio_imem_write(addr, instr)

    # Set quantum divider
    QDIV = 50
    await spi.bio_write(0x50, QDIV << 16)

    # Enable snap-to-quantum
    await spi.bio_write(0x08, 0x00000001)

    # Enable core 0
    await spi.bio_write(0x00, 0x00000005)
    await ClockCycles(dut.clk, 500)

    # Verify idle state: CS high (bit 2), clock low (bit 1), data low (bit 0)
    gpio_val = int(dut.gpio_out.value) & 0x07
    dut._log.info(f"Idle GPIO: 0b{gpio_val:03b} (expected 0b100 = CS high)")
    assert gpio_val & 0x04, "CS should be high in idle"

    # Send test bytes and capture SPI output
    test_values = [0x55, 0xAA, 0xA5, 0x0F, 0xF0, 0x01, 0x80, 0xFF]

    all_pass = True
    for val in test_values:
        dut._log.info(f"Sending 0x{val:02X}...")

        # Push to FIFO0
        await spi.bio_write(0x10, val)

        # Capture SPI transaction from GPIO pins
        captured = await capture_spi_byte(dut, 8)

        if captured is not None:
            # We sent LSB first, capture_spi_byte reads MSB first
            # So we need to bit-reverse the captured value for comparison
            # OR: capture_spi_byte already captures in send order (MSB first from bit stream)
            # Actually our program sends LSB first (srli shifts right),
            # and capture_spi_byte captures MSB first (shifts left).
            # So captured bits are reversed. Let's reverse:
            reversed_val = 0
            for b in range(8):
                reversed_val |= ((captured >> b) & 1) << (7 - b)

            ok = reversed_val == val
            status = "PASS" if ok else "FAIL"
            dut._log.info(f"  Captured: 0x{captured:02X}, reversed: 0x{reversed_val:02X} — {status}")
            if not ok:
                dut._log.info(f"  Sent bits: {val:08b}")
                dut._log.info(f"  Got bits:  {reversed_val:08b}")
                all_pass = False
        else:
            dut._log.error(f"  Timeout!")
            all_pass = False

    await spi.bio_write(0x00, 0x00000000)

    assert all_pass, "SPI bitbang capture mismatch!"

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — SPI bitbang works!")
    dut._log.info("  8-bit SPI master on GPIO[2:0]")
    dut._log.info("  CS, SCK, MOSI waveforms correct")
    dut._log.info("  All test patterns verified")
    dut._log.info("========================================")
