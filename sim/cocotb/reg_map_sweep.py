@cocotb.test()
async def test_register_defaults(dut):
    """Verify all registers read correct defaults after reset."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)
    
    # Chip ID
    val = await spi.bio_read(0xFC)
    assert val == 0x0B100002, f"Chip ID: 0x{val:08X}"
    
    # Control = 0 (cores disabled)
    val = await spi.bio_read(0x00)
    assert val == 0, f"Control: 0x{val:08X}"
    
    # GPIO out/dir = 0
    val = await spi.bio_read(0x84)
    assert val == 0, f"GPIO out: 0x{val:08X}"
    val = await spi.bio_read(0x88)
    assert val == 0, f"GPIO dir: 0x{val:08X}"
    
    # FIFO levels = 0
    val = await spi.bio_read(0x0C)
    assert val == 0, f"FIFO levels: 0x{val:08X}"
    
    # Inversion registers = 0
    for addr, name in [(0x64, "oe_inv"), (0x68, "out_inv"), (0x6C, "in_inv")]:
        val = await spi.bio_read(addr)
        assert val == 0, f"{name}: 0x{val:08X}"
