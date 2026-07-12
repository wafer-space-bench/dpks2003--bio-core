# chip_top_tb.py — chip-level testbench for BIO-lite (RTL chip_top or GL netlist)
#
# Pad map (from chip_core.sv):
#   input_PAD[0]=spi_sclk  [1]=spi_mosi  [2]=spi_cs_n  [3]=spare
#   bidir_PAD[0]=spi_miso  [1]=irq0  [2]=irq1  [3..34]=gpio[0..31]
#
# Test ladder (each stage localizes failures):
#   1. smoke: CHIP_ID read     (pads + reset + bridge + read path)
#   2. reg write/read          (write datapath, flops out of X)
#   3. IMEM both halves        (SRAM macros + addr[8] path post-synthesis)
#   4. core executes program   (ICG + fetch + stall + GPIO pads: whole chip)
#   5. IRQ pad                 (event -> irqmask -> bidir_PAD[1])
#
# Usage:
#   RTL chip-top:  python3 chip_top_tb.py                     (needs sv2v outputs)
#   GL synthesis:  GL=1 POWER=0 NETLIST=<chip_top.nl.v>  python3 chip_top_tb.py
#   GL final PnR:  GL=1 POWER=1 NETLIST=<chip_top.pnl.v> python3 chip_top_tb.py

import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer
from spi_master import SPIMaster

gl    = os.getenv("GL", "0") not in ("0", "", "False", "false")
power = os.getenv("POWER", "0") == "1"

GPIO_BASE = 3          # bidir_PAD[3] = gpio[0]
IRQ0_PAD  = 1
NUM_BIDIR = 44

# bunnie's hello_world — walking 1-bit across GPIO with quantum timing.
# Identical word-for-word to test_bunnie.py (keep them in sync!).
HELLO_WORLD_PROGRAM = [
    0xFFF00293,  # [0x00] addi x5, x0, -1        li t0, -1 (0xFFFFFFFF)
    0x00028C13,  # [0x04] addi x24, x5, 0        mv x24, t0 (all output)
    0x00100513,  # [0x08] addi x10, x0, 1        li a0, 1
    # loop (0x0C):
    0x00050A93,  # [0x0C] addi x21, x10, 0       mv x21, a0 (GPIO write)
    0x00151513,  # [0x10] slli x10, x10, 1       slli a0, a0, 1
    0x00051463,  # [0x14] bne x10, x0, +8        bne a0, zero, skip
    0x00100513,  # [0x18] addi x10, x0, 1        li a0, 1 (reset)
    # skip (0x1C):
    0x00000A13,  # [0x1C] addi x20, x0, 0        mv x20, zero (quantum wait)
    0xFEDFF06F,  # [0x20] jal x0, -20            j loop (0x0C)
    0x00000013,  # [0x24] nop
]

# Test-4 register values — MIRROR test_bunnie.py exactly:
T4_QDIV         = 100
T4_DIV0_VALUE   = T4_QDIV << 16     # 0x00640000
T4_ENABLE_VALUE = 0x00000005        # restart0 | en0
# Timing: quantum = T4_QDIV * 100ns = 10us per walker step at 10 MHz chip clock.


# ---------------- packed-vector pad adapters ----------------
# The synthesized netlist has input_PAD/bidir_PAD as packed vectors;
# cocotb 2.x cannot index packed vectors bit-wise (dut.input_PAD[0] fails).
# These adapters drive/read whole vectors while presenting per-bit .value
# handles compatible with SPIMaster.

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
    """Read-only view of one bit of a packed output vector.
    .value returns '0'/'1'/'x'/'z' as a str; int('x') raises ValueError,
    which SPIMaster uses for X detection."""
    def __init__(self, handle, i):
        self.handle = handle
        self.i = i

    @property
    def value(self):
        s = str(self.handle.value)              # MSB-first binstr
        return s[len(s) - 1 - self.i]


# ---------------- helpers ----------------

async def start_up(dut):
    if gl and power:
        dut.VDD.value = 1
        dut.VSS.value = 0

    cocotb.start_soon(Clock(dut.clk_PAD, 100, unit="ns").start())   # 10 MHz

    # input_PAD idle: sclk=0, mosi=0, cs_n=1, spare=0  -> 0b0100
    in_bus = PadInBus(dut.input_PAD, width=4, init=0b0100)

    dut.rst_n_PAD.value = 0
    await Timer(1000, unit="ns")
    dut.rst_n_PAD.value = 1
    await Timer(2000, unit="ns")

    return SPIMaster(dut,
                     sclk=in_bus.bit(0), mosi=in_bus.bit(1),
                     cs_n=in_bus.bit(2),
                     miso=PadOutBit(dut.bidir_PAD, 0))


def pad_bit(dut, i):
    """0/1, or None if x/z."""
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


# ---------------- tests ----------------

@cocotb.test()
async def test_1_smoke_chip_id(dut):
    """Pads + reset + SPI bridge + register read path."""
    spi = await start_up(dut)
    dut._log.info(f"bidir_PAD after reset = {str(dut.bidir_PAD.value)}")
    chip_id = await spi.bio_read_double(0xFC)
    dut._log.info(f"CHIP_ID = 0x{chip_id:08X}  (miso X seen: {spi.x_seen})")
    assert chip_id == 0x0B100002, f"CHIP_ID mismatch: 0x{chip_id:08X}"
    assert not spi.x_seen, "MISO went X during data phase — netlist init/reset problem"


@cocotb.test()
async def test_2_reg_write_read(dut):
    """Write datapath through the pads."""
    spi = await start_up(dut)
    await spi.bio_write(0x50, 0x00C0FFEE)
    got = await spi.bio_read_double(0x50)
    assert got == 0x00C0FFEE, f"DIV0 readback: 0x{got:08X}"


@cocotb.test()
async def test_3_imem_both_halves(dut):
    """Placed SRAM macros + addr[8] path. Both cores.
    Watch log for 8x 'CEN is just reset, memory is operational'."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)          # cores disabled

    vec = {0: 0x11110000, 3: 0x11110003, 300: 0x22220300, 511: 0x222201FF}
    for core in (0, 1):
        await spi.bio_write(0xE0, core)
        for a, v in vec.items():
            await spi.bio_imem_write(a, v ^ (core << 28))
        for a, v in vec.items():
            got = await spi.bio_imem_read(a)
            exp = v ^ (core << 28)
            assert got == exp, f"core{core} word {a}: 0x{got:08X} != 0x{exp:08X}"
    dut._log.info("IMEM low+high halves OK on both cores (8 macros reachable)")


@cocotb.test()
async def test_4_core_executes(dut):
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)          # cores disabled
    await spi.bio_write(0xE0, 0)

    # Load ONCE, verify while still disabled (host owns IMEM only when disabled)
    await spi.bio_imem_burst_write(0, HELLO_WORLD_PROGRAM)
    for a in (0, 1, 8, 9):
        got = await spi.bio_imem_read(a)
        assert got == HELLO_WORLD_PROGRAM[a], \
            f"burst load failed at GL: word {a} = 0x{got:08X}"
    dut._log.info("program verified in IMEM")

    await spi.bio_write(0x50, T4_DIV0_VALUE)
    await spi.bio_write(0x00, T4_ENABLE_VALUE)     # NOW enable
    await Timer(50_000, unit="ns")

    gdir = await spi.bio_read_double(0x88)
    dut._log.info(f"gpio_dir after enable = 0x{gdir:08X}")
    assert gdir == 0xFFFFFFFF, f"gpio_dir=0x{gdir:08X}: core did not execute setup"

    seen = []
    for i in range(28):
        v = read_gpio(dut)
        if v != 0 and (v & (v - 1)) == 0 and (not seen or v != seen[-1]):
            seen.append(v)
        if i < 4:
            dut._log.info(f"  sample[{i}] bidir_PAD = {str(dut.bidir_PAD.value)}")
        await Timer(3_000, unit="ns")

    dut._log.info(f"one-hot GPIO samples: {[hex(s) for s in seen]}")
    assert len(seen) >= 4, f"walking-1 not observed ({len(seen)} samples)"

    
@cocotb.test()
async def test_5_irq_pad(dut):
    """Host event -> irqmask -> irq[0] pad. With event hygiene + X-aware asserts."""
    spi = await start_up(dut)
    await spi.bio_write(0x00, 0x00000000)          # cores off
    await spi.bio_write(0x3C, 0x00FFFFFF)          # clear all host-clearable events
    await Timer(1_000, unit="ns")

    ev = await spi.bio_read_double(0x40)
    dut._log.info(f"aggregated_events after clear = 0x{ev:08X}")

    await spi.bio_write(0x70, 0x00000001)          # irqmask0 = event bit 0
    await Timer(1_000, unit="ns")

    b = pad_bit(dut, IRQ0_PAD)
    dut._log.info(f"IRQ0 pad before event = {b!r}")
    assert b == 0, f"IRQ0 not low before event (got {b!r}; None = X/Z on the pad!)"

    await spi.bio_write(0x38, 0x00000001)          # host event set, bit 0
    await Timer(1_000, unit="ns")
    b = pad_bit(dut, IRQ0_PAD)
    assert b == 1, f"IRQ0 did not assert (got {b!r})"

    await spi.bio_write(0x3C, 0x00000001)          # host event clear
    await Timer(1_000, unit="ns")
    b = pad_bit(dut, IRQ0_PAD)
    assert b == 0, f"IRQ0 did not deassert (got {b!r})"


# ---------------- runner ----------------

if __name__ == "__main__":
    from cocotb_tools.runner import get_runner

    pdk_root = Path(os.getenv("PDK_ROOT", "../../gf180mcu")).resolve()
    pdk      = os.getenv("PDK", "gf180mcuD")
    scl      = os.getenv("SCL", "gf180mcu_fd_sc_mcu7t5v0")

    # ciel layout support: PDK_ROOT may point above the ciel version dir
    base = pdk_root / pdk
    if not base.exists():
        hits = sorted(pdk_root.glob(f"ciel/*/versions/*/{pdk}"))
        assert hits, f"cannot locate {pdk} under {pdk_root}"
        base = hits[-1]

    lib_v  = base / "libs.ref" / scl / "verilog"
    pad_v  = base / "libs.ref" / "gf180mcu_fd_io" / "verilog"
    sram_v = base / "libs.ref" / "gf180mcu_fd_ip_sram" / "verilog"
    src    = Path("../../src").resolve()
    ip     = Path("../../ip").resolve()

    common = [
        lib_v / "primitives.v",
        lib_v / f"{scl}.v",
        pad_v / "gf180mcu_fd_io.v",
        sram_v / "gf180mcu_fd_ip_sram__sram512x8m8wm1.v",
        *sorted(ip.glob("*/vh/*.v")),        # ws_ip / dpk_ip macro stubs
    ]

    defines = {"FUNCTIONAL": 1}

    if gl:
        nl = Path(os.environ["NETLIST"]).resolve()
        sources = [nl] + common
        if power:
            defines["USE_POWER_PINS"] = 1
    else:
        sources = [
            Path("chip_top.v").resolve(),        # sv2v outputs
            Path("chip_core.v").resolve(),
            Path("bio_lite_top.v").resolve(),
            Path("bio_lite.v").resolve(),
            Path("spi_bio_bridge.v").resolve(),
            src / "spi_slave.v",
            src / "picorv32.v",
            src / "regfifo.v",
            src / "pio_divider.v",
            src / "rst_sync.v",
            src / "sram_512x32.v",
        ] + common
        defines["USE_GF180_SRAM"] = 1
        defines["USE_GF180"]  = 1

    runner = get_runner("icarus")
    runner.build(
        sources=sources,
        hdl_toplevel="chip_top",
        defines=defines,
        build_args=["-g2012"],
        always=True,
    )
    runner.test(
        hdl_toplevel="chip_top",
        test_module=os.getenv("TEST_MODULE", "chip_top_tb"),
        testcase=os.getenv("TESTCASE", None),
        waves=bool(os.getenv("WAVES", "0") == "1"),
    )
    
