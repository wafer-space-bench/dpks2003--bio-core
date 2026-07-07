
import cocotb
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer

class SPIMaster:
    """SPI master driver for BIO-lite testing.
    Bit-bangs SPI Mode 0 (CPOL=0, CPHA=0) on the DUT's SPI pins.
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
            await Timer(self.spi_half_period, units="ns")

            self.dut.spi_sclk.value = 1
            await Timer(1, units="ns")
            rx_bit = int(self.dut.spi_miso.value)
            rx_byte = (rx_byte << 1) | rx_bit
            await Timer(self.spi_half_period - 1, units="ns")

            self.dut.spi_sclk.value = 0

        return rx_byte

    async def transfer(self, tx_bytes):
        """Full SPI transaction: assert CS, shift bytes, deassert CS."""
        rx_bytes = []

        self.dut.spi_cs_n.value = 0
        await Timer(self.spi_half_period, units="ns")

        for tx in tx_bytes:
            rx = await self._spi_byte(tx)
            rx_bytes.append(rx)

        await Timer(self.spi_half_period, units="ns")
        self.dut.spi_cs_n.value = 1
        await Timer(self.spi_half_period * 2, units="ns")

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
        """IMEM_WRITE: cmd=0x04"""
        tx = [
            0x04, word_addr,
            (instruction >> 24) & 0xFF,
            (instruction >> 16) & 0xFF,
            (instruction >> 8) & 0xFF,
            instruction & 0xFF,
        ]
        await self.transfer(tx)

    async def bio_imem_read(self, word_addr):
        """IMEM_READ: cmd=0x05. Returns 32-bit instruction."""
        tx = [0x05, word_addr, 0x00, 0x00, 0x00, 0x00]
        rx = await self.transfer(tx)
        return (rx[2] << 24) | (rx[3] << 16) | (rx[4] << 8) | rx[5]
    
    async def bio_read_double(self, addr):
        """Double-read: first read triggers, second returns data."""
        await self.bio_read(addr)
        return await self.bio_read(addr)
