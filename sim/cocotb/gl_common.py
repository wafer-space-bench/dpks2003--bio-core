# gl_common.py — shared pad adapters + startup for chip_top-level testbenches
# (used by chip_top_tb.py ladder and chip_top_gl_ext.py soak suite)

import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer
from spi_master import SPIMaster

gl    = os.getenv("GL", "0") not in ("0", "", "False", "false")
power = os.getenv("POWER", "0") == "1"

GPIO_BASE = 3          # bidir_PAD[3] = gpio[0]
IRQ0_PAD  = 1
IRQ1_PAD  = 2


class PadInBus:
    """Drives a packed input vector via a shadow register (race-free RMW)."""
    def __init__(self, handle, width, init=0):
        self.handle = handle
        self.width = width
        self.shadow = init
        self.handle.value = init

    def set_bit(self, i, v):
        if v:
            self.shadow |= (1 << i)
        else:
            self.shadow &= ~(1 << i)
        self.handle.value = self.shadow

    def bit(self, i):
        bus = self

        class _Bit:
            @property
            def value(self):
                return (bus.shadow >> i) & 1

            @value.setter
            def value(self, v):
                bus.set_bit(i, int(v))

        return _Bit()


class PadOutBit:
    """Read-only single bit of a packed output vector ('0'/'1'/'x'/'z' str)."""
    def __init__(self, handle, i):
        self.handle = handle
        self.i = i

    @property
    def value(self):
        s = str(self.handle.value)
        return s[len(s) - 1 - self.i]


class BidirDriver:
    """Drives selected bits of bidir_PAD from the TB (for GPIO-input tests).
    Undriven bits stay 'z' so the chip can drive them."""
    def __init__(self, handle, width=44):
        self.handle = handle
        self.width = width
        self.drive = {}            # bit -> 0/1

    def _apply(self):
        s = ""
        for i in range(self.width - 1, -1, -1):
            s += str(self.drive[i]) if i in self.drive else "z"
        self.handle.value = cocotb.types.LogicArray(s)

    def set_bit(self, i, v):
        self.drive[i] = int(v)
        self._apply()

    def release_bit(self, i):
        self.drive.pop(i, None)
        self._apply()

    def release_all(self):
        self.drive = {}
        self._apply()


def pad_bit(dut, i):
    s = str(dut.bidir_PAD.value)
    c = s[len(s) - 1 - i]
    return int(c) if c in "01" else None


def read_gpio(dut, width=32):
    s = str(dut.bidir_PAD.value)
    v = 0
    for i in range(width):
        if s[len(s) - 1 - (GPIO_BASE + i)] == "1":
            v |= 1 << i
    return v


async def start_up(dut):
    if gl and power:
        dut.VDD.value = 1
        dut.VSS.value = 0

    cocotb.start_soon(Clock(dut.clk_PAD, 100, unit="ns").start())   # 10 MHz

    in_bus = PadInBus(dut.input_PAD, width=4, init=0b0100)          # cs_n=1 idle

    dut.rst_n_PAD.value = 0
    await Timer(1000, unit="ns")
    dut.rst_n_PAD.value = 1
    await Timer(2000, unit="ns")

    spi = SPIMaster(dut,
                    sclk=in_bus.bit(0), mosi=in_bus.bit(1),
                    cs_n=in_bus.bit(2),
                    miso=PadOutBit(dut.bidir_PAD, 0))
    return spi
