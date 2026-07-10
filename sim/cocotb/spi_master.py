import cocotb
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer

class SPIMaster:
    """SPI master driver for BIO-lite testing. Bit-bangs SPI Mode 0.

    Two usage levels:
      bio_lite_top:  SPIMaster(dut)                       (default pin names)
      chip_top pads: SPIMaster(dut, sclk=.., mosi=.., cs_n=.., miso=..)
                     (any object exposing a .value attribute works)

    IMEM command map:
      0x02 REG_WRITE   0x03 REG_READ
      0x04/0x05 IMEM write/read words 0-255   (addr[8]=0)
      0x06/0x07 IMEM write/read words 256-511 (addr[8]=1)
    IMEM writes auto-increment in the bridge -> burst loading supported.
    """

    def __init__(self, dut, spi_period_ns=1000,
                 sclk=None, mosi=None, cs_n=None, miso=None):
        self.dut = dut
        self.spi_half_period = spi_period_ns // 2

        self.sclk = sclk if sclk is not None else dut.spi_sclk
        self.mosi = mosi if mosi is not None else dut.spi_mosi
        self.cs_n = cs_n if cs_n is not None else dut.spi_cs_n
        self.miso = miso if miso is not None else dut.spi_miso

        self.x_seen = False   # set if MISO was x/z during any data bit

        self.cs_n.value = 1
        self.sclk.value = 0
        self.mosi.value = 0

    def _read_miso(self):
        """X-safe MISO sample: 0/1; flags x/z."""
        v = self.miso.value
        try:
            return int(v)
        except (ValueError, TypeError):
            self.x_seen = True
            return 0

    async def _spi_byte(self, tx_byte):
        rx_byte = 0
        for bit in range(7, -1, -1):
            self.mosi.value = (tx_byte >> bit) & 1
            await Timer(self.spi_half_period, unit="ns")

            self.sclk.value = 1
            await Timer(1, unit="ns")
            rx_byte = (rx_byte << 1) | self._read_miso()
            await Timer(self.spi_half_period - 1, unit="ns")

            self.sclk.value = 0
        return rx_byte

    async def transfer(self, tx_bytes):
        rx_bytes = []
        self.cs_n.value = 0
        await Timer(self.spi_half_period, unit="ns")
        for tx in tx_bytes:
            rx_bytes.append(await self._spi_byte(tx))
        await Timer(self.spi_half_period, unit="ns")
        self.cs_n.value = 1
        await Timer(self.spi_half_period * 2, unit="ns")
        return rx_bytes

    async def bio_write(self, addr, data):
        """REG_WRITE: cmd=0x02"""
        await self.transfer([0x02, addr,
            (data >> 24) & 0xFF, (data >> 16) & 0xFF,
            (data >> 8) & 0xFF, data & 0xFF])

    async def bio_read(self, addr):
        """REG_READ: cmd=0x03. Returns 32-bit value."""
        rx = await self.transfer([0x03, addr, 0, 0, 0, 0])
        return (rx[2] << 24) | (rx[3] << 16) | (rx[4] << 8) | rx[5]

    async def bio_imem_write(self, word_addr, instruction):
        assert 0 <= word_addr <= 511, f"IMEM address out of range: {word_addr}"
        cmd = 0x06 if word_addr >= 256 else 0x04
        await self.transfer([cmd, word_addr & 0xFF,
            (instruction >> 24) & 0xFF, (instruction >> 16) & 0xFF,
            (instruction >> 8) & 0xFF, instruction & 0xFF])

    async def bio_imem_read(self, word_addr):
        assert 0 <= word_addr <= 511, f"IMEM address out of range: {word_addr}"
        cmd = 0x07 if word_addr >= 256 else 0x05
        rx = await self.transfer([cmd, word_addr & 0xFF, 0, 0, 0, 0])
        return (rx[2] << 24) | (rx[3] << 16) | (rx[4] << 8) | rx[5]

    async def bio_imem_burst_write(self, start_addr, words):
        assert 0 <= start_addr <= 511
        assert start_addr + len(words) <= 512, "burst past end of IMEM"
        cmd = 0x06 if start_addr >= 256 else 0x04
        tx = [cmd, start_addr & 0xFF]
        for w in words:
            tx += [(w >> 24) & 0xFF, (w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF]
        await self.transfer(tx)

    async def bio_read_double(self, addr):
        """Workaround for first-read-stale symptom — root cause still open."""
        await self.bio_read(addr)
        return await self.bio_read(addr)
