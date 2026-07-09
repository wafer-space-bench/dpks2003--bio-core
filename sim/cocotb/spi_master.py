import cocotb
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer

class SPIMaster:
    """SPI master driver for BIO-lite testing.
    Bit-bangs SPI Mode 0 (CPOL=0, CPHA=0) on the DUT's SPI pins.

    IMEM command map (bridge protocol):
      0x02 = REG_WRITE            0x03 = REG_READ
      0x04 = IMEM_WRITE lo half   0x05 = IMEM_READ lo half   (words 0-255,  addr[8]=0)
      0x06 = IMEM_WRITE hi half   0x07 = IMEM_READ hi half   (words 256-511, addr[8]=1)
    IMEM writes auto-increment in the bridge -> burst loading supported.
    """

    def __init__(self, dut, spi_period_ns=1000):
        self.dut = dut
        self.spi_half_period = spi_period_ns // 2

        self.dut.spi_cs_n.value = 1
        self.dut.spi_sclk.value = 0
        self.dut.spi_mosi.value = 0

    async def _spi_byte(self, tx_byte):
        """Shift out 8 bits on MOSI, capture 8 bits from MISO. Mode 0."""
        rx_byte = 0
        for bit in range(7, -1, -1):
            self.dut.spi_mosi.value = (tx_byte >> bit) & 1
            await Timer(self.spi_half_period, unit="ns")

            self.dut.spi_sclk.value = 1
            await Timer(1, unit="ns")
            rx_bit = int(self.dut.spi_miso.value)
            rx_byte = (rx_byte << 1) | rx_bit
            await Timer(self.spi_half_period - 1, unit="ns")

            self.dut.spi_sclk.value = 0

        return rx_byte

    async def transfer(self, tx_bytes):
        """Full SPI transaction: assert CS, shift bytes, deassert CS."""
        rx_bytes = []

        self.dut.spi_cs_n.value = 0
        await Timer(self.spi_half_period, unit="ns")

        for tx in tx_bytes:
            rx = await self._spi_byte(tx)
            rx_bytes.append(rx)

        await Timer(self.spi_half_period, unit="ns")
        self.dut.spi_cs_n.value = 1
        await Timer(self.spi_half_period * 2, unit="ns")

        return rx_bytes

    async def bio_write(self, addr, data):
        """REG_WRITE: cmd=0x02"""
        tx = [
            0x02, addr,
            (data >> 24) & 0xFF,
            (data >> 16) & 0xFF,
            (data >> 8) & 0xFF,
            data & 0xFF,
        ]
        await self.transfer(tx)

    async def bio_read(self, addr):
        """REG_READ: cmd=0x03. Returns 32-bit value."""
        tx = [0x03, addr, 0x00, 0x00, 0x00, 0x00]
        rx = await self.transfer(tx)
        return (rx[2] << 24) | (rx[3] << 16) | (rx[4] << 8) | rx[5]

    async def bio_imem_write(self, word_addr, instruction):
        """IMEM_WRITE: cmd 0x04 (words 0-255) / 0x06 (words 256-511)."""
        assert 0 <= word_addr <= 511, f"IMEM word address out of range: {word_addr}"
        cmd = 0x06 if word_addr >= 256 else 0x04
        tx = [
            cmd, word_addr & 0xFF,
            (instruction >> 24) & 0xFF,
            (instruction >> 16) & 0xFF,
            (instruction >> 8) & 0xFF,
            instruction & 0xFF,
        ]
        await self.transfer(tx)

    async def bio_imem_read(self, word_addr):
        """IMEM_READ: cmd 0x05 (words 0-255) / 0x07 (words 256-511). Returns 32-bit word."""
        assert 0 <= word_addr <= 511, f"IMEM word address out of range: {word_addr}"
        cmd = 0x07 if word_addr >= 256 else 0x05
        tx = [cmd, word_addr & 0xFF, 0x00, 0x00, 0x00, 0x00]
        rx = await self.transfer(tx)
        return (rx[2] << 24) | (rx[3] << 16) | (rx[4] << 8) | rx[5]

    async def bio_imem_burst_write(self, start_addr, words):
        """Burst IMEM load: one CS assertion, cmd+addr once, then N words.
        The bridge auto-increments the word address after each 32-bit word,
        carrying across the 255->256 boundary. Max 512 words from addr 0."""
        assert 0 <= start_addr <= 511, f"IMEM word address out of range: {start_addr}"
        assert start_addr + len(words) <= 512, "burst would run past end of IMEM"
        cmd = 0x06 if start_addr >= 256 else 0x04
        tx = [cmd, start_addr & 0xFF]
        for w in words:
            tx += [(w >> 24) & 0xFF, (w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF]
        await self.transfer(tx)

    async def bio_read_double(self, addr):
        """Double-read: first read triggers, second returns data.
        NOTE: workaround for the first-read-stale symptom — root cause
        still open (see SPI bridge read-path timing). Remove once fixed."""
        await self.bio_read(addr)
        return await self.bio_read(addr)