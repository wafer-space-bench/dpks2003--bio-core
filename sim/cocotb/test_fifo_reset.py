# test_fifo_restart.py
#
# Verifies the per-core FIFO restart policy (bio_bdma semantics):
#   - restart of core k clears FIFO k ONLY
#   - FIFOs 2..3 have no owning core -> untouched by any core restart
#   - host FIFO_CLR (0x48) clears exactly the masked FIFOs
#   - surviving FIFOs keep their DATA, not just their levels
#
# Failure signatures on known-bad RTL:
#   old bug        : restart core 1 clears nothing; restart core 0 clears ALL
#   |restart "fix" : restart core 1 clears everything incl. core 0's data
#
# Register map used:
#   0x00 = {clkdiv_restart[1:0], restart[1:0], en[1:0]}
#          -> restart core0 = 0x04, restart core1 = 0x08 (en bits stay 0)
#   0x10/0x14/0x18/0x1C = host push into FIFO 0/1/2/3
#   0x20/0x24/0x28/0x2C = host pull from FIFO 0/1/2/3 (read pops!)
#   0x0C = FIFO levels, 4 bits per FIFO: {lvl3, lvl2, lvl1, lvl0}
#   0x48 = FIFO_CLR, bit k clears FIFO k

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles
from spi_master import SPIMaster

# Distinct payloads so we can prove data integrity, not just levels
PAYLOAD = [0xF1F00000, 0xF1F10001, 0xF1F20002, 0xF1F30003]


async def setup_dut(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    dut.reset_n.value = 0
    dut.gpio_in.value = 0
    await ClockCycles(dut.clk, 10)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 10)
    spi = SPIMaster(dut)
    await spi.bio_write(0x00, 0x00000000)   # both cores disabled throughout
    await ClockCycles(dut.clk, 5)
    return spi


async def get_levels(spi):
    """Return [lvl0, lvl1, lvl2, lvl3] from reg 0x0C."""
    v = await spi.bio_read(0x0C)
    return [(v >> (4 * i)) & 0xF for i in range(4)]


async def fill_all(spi):
    """Push one distinct word into each FIFO."""
    for i, addr in enumerate([0x10, 0x14, 0x18, 0x1C]):
        await spi.bio_write(addr, PAYLOAD[i])


async def check_levels(spi, expected, ctx, dut):
    got = await get_levels(spi)
    assert got == expected, f"{ctx}: levels {got}, expected {expected}"
    dut._log.info(f"{ctx}: levels {got} OK")


@cocotb.test()
async def test_restart_core1_clears_only_fifo1(dut):
    """Restart of core 1 must clear FIFO 1 and nothing else."""
    spi = await setup_dut(dut)

    await fill_all(spi)
    await check_levels(spi, [1, 1, 1, 1], "after fill", dut)

    await spi.bio_write(0x00, 0x00000008)   # restart[1]=1, en=00
    await ClockCycles(dut.clk, 10)

    # Old bug: [1,1,1,1] (nothing cleared). |restart: [0,0,1,1]-ish (F0 lost).
    await check_levels(spi, [1, 0, 1, 1], "after restart core1", dut)

    # Survivors must still hold their ORIGINAL data (read pops the FIFO)
    assert await spi.bio_read(0x20) == PAYLOAD[0], "FIFO0 data corrupted"
    assert await spi.bio_read(0x28) == PAYLOAD[2], "FIFO2 data corrupted"
    assert await spi.bio_read(0x2C) == PAYLOAD[3], "FIFO3 data corrupted"


@cocotb.test()
async def test_restart_core0_clears_only_fifo0(dut):
    """Restart of core 0 must clear FIFO 0 and nothing else."""
    spi = await setup_dut(dut)

    await fill_all(spi)
    await spi.bio_write(0x00, 0x00000004)   # restart[0]=1
    await ClockCycles(dut.clk, 10)

    # Old bug: [0,0,0,0] (restart[0] nuked ALL four FIFOs).
    await check_levels(spi, [0, 1, 1, 1], "after restart core0", dut)

    assert await spi.bio_read(0x24) == PAYLOAD[1], "FIFO1 data corrupted"
    assert await spi.bio_read(0x28) == PAYLOAD[2], "FIFO2 data corrupted"
    assert await spi.bio_read(0x2C) == PAYLOAD[3], "FIFO3 data corrupted"


@cocotb.test()
async def test_restart_both_cores(dut):
    """Simultaneous restart of both cores clears FIFOs 0 and 1, spares 2 and 3."""
    spi = await setup_dut(dut)

    await fill_all(spi)
    await spi.bio_write(0x00, 0x0000000C)   # restart[1:0]=11
    await ClockCycles(dut.clk, 10)

    await check_levels(spi, [0, 0, 1, 1], "after restart both", dut)


@cocotb.test()
async def test_host_fifo_clr_is_precise(dut):
    """Host FIFO_CLR (0x48) clears exactly the masked FIFOs — incl. 2/3,
    which no core restart can reach."""
    spi = await setup_dut(dut)

    await fill_all(spi)
    await spi.bio_write(0x48, 0x00000004)   # clear FIFO 2 only
    await ClockCycles(dut.clk, 10)
    await check_levels(spi, [1, 1, 0, 1], "after FIFO_CLR mask=0b0100", dut)

    await spi.bio_write(0x48, 0x00000008)   # clear FIFO 3 only
    await ClockCycles(dut.clk, 10)
    await check_levels(spi, [1, 1, 0, 0], "after FIFO_CLR mask=0b1000", dut)

    # FIFOs 0/1 untouched through it all, data intact
    assert await spi.bio_read(0x20) == PAYLOAD[0], "FIFO0 data corrupted"
    assert await spi.bio_read(0x24) == PAYLOAD[1], "FIFO1 data corrupted"
