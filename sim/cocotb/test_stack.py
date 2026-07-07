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


def test_sum(a):
    """The recursive function: sum = N + N-1 + N-2 + ... + 0"""
    if a == 0:
        return 0
    return a + test_sum(a - 1)


@cocotb.test()
async def test_stack(dut):
    """bunnie's stack_test: recursive sum computation using stack.
    
    Original: 3 cores cascaded (core0→FIFO1→core1→FIFO2→core2→FIFO3)
    Adapted: 2 cores cascaded (core0→FIFO1→core1→FIFO3→host reads)
    
    Core 0: reads N from FIFO0, computes test_sum(N) recursively, pushes to FIFO1
    Core 1: reads from FIFO1, computes test_sum again, pushes to FIFO3
    
    Host pushes 3, expects test_sum(test_sum(3)) = test_sum(6) = 21
    Host pushes 5, expects test_sum(test_sum(5)) = test_sum(15) = 120
    Host pushes 4, expects test_sum(test_sum(4)) = test_sum(10) = 55
    
    This tests: stack pointer, sw/lw instructions, jal/ret (function calls),
    bne (conditional branch), addi, add — deep PicoRV32 functionality.
    
    From arith.rs lines 102-180."""
    await reset_dut(dut)
    spi = SPIMaster(dut, spi_period_ns=1000)

    dut._log.info("=== bunnie's stack test (2-core) ===")

    # Core 0 program (stack_test0_code from arith.rs):
    # Reads FIFO0 (x16), computes recursive sum, pushes result to FIFO1 (x17)
    #
    #   li   sp, 0x800        → initialize stack pointer
    # entry:
    #   mv   a0, x16          → read argument from FIFO0
    #   jal  ra, sum_func     → call recursive sum
    #   mv   x17, a0          → push result to FIFO1
    #   j    entry            → loop
    # sum_func:
    #   addi sp, sp, -8       → allocate stack frame
    #   sw   ra, 4(sp)        → save return address
    #   sw   a0, 0(sp)        → save argument
    #   bne  a0, x0, recurse  → if a0 != 0, recurse
    #   addi sp, sp, 8        → restore stack (a0 already 0)
    #   ret                   → return 0
    # recurse:
    #   addi a0, a0, -1       → a0 = a0 - 1
    #   jal  ra, sum_func     → recursive call
    #   lw   t0, 0(sp)        → restore saved argument
    #   add  a0, t0, a0       → a0 = saved_arg + recursive_result
    #   lw   ra, 4(sp)        → restore return address
    #   addi sp, sp, 8        → restore stack
    #   ret
    #
    # Register mapping: sp=x2, ra=x1, a0=x10, t0=x5

    # Encodings:
    #   li sp, 0x800 = addi x2, x0, 0x800 → but 0x800=2048 doesn't fit in 12-bit signed!
    #   Need: lui x2, 1; (x2 = 0x1000) then: addi x2, x2, -0x800... no, that's wrong.
    #   Actually bunnie writes "li sp, 0x800" which the assembler handles as:
    #   lui x2, 0x1         → x2 = 0x1000
    #   addi x2, x2, -2048  → x2 = 0x1000 - 0x800 = 0x800
    #   Wait — that's 0x1000 - 0x800 = 0x800. But -2048 = -0x800.
    #   0x1000 + (-0x800) = 0x800. Yes!
    #
    #   But simpler: lui x2, 1 → x2 = 0x1000. Then we can just use 0x1000 as stack.
    #   Actually for the test, stack at 0x800 or 0x1000 doesn't matter — just need valid IMEM.
    #   Let's use: lui x2, 1 (x2=0x1000, but IMEM is only 4K so this is top of memory — fine)
    #   Actually MEM_SIZE_BYTES=0x1000, so sp at 0x800 is in the middle. Safe for recursion.
    #
    #   Simpler approach: addi x2, x0, 0x7FF (max positive 12-bit = 2047 = 0x7FF)
    #   Close enough to 0x800. Or just use 0x400 which fits easily.

    # Let me encode each instruction carefully:

    # li sp, 0x7F0 = addi x2, x0, 0x7F0    (close to 0x800, fits in 12 bits)
    # 0x7F0 << 20 | 0 << 15 | 0 << 12 | 2 << 7 | 0x13
    # = 0x7F000113

    # mv a0, x16 = addi x10, x16, 0 = (16<<15) | (10<<7) | 0x13 = 0x00080513

    # jal ra, sum_func: ra=x1, offset from 0x08 to 0x10 = +8
    # jal x1, +8:
    #   imm = 8, bit20=0, bits10:1=0000000100, bit11=0, bits19:12=00000000
    #   = 0_0000000100_0_00000000 | (1<<7) | 0x6F
    #   = (0b0000000100 << 21) | (1<<7) | 0x6F
    #   = (4 << 21) | 0x80 | 0x6F = 0x008000EF

    # mv x17, a0 = addi x17, x10, 0 = (10<<15) | (17<<7) | 0x13 = 0x00050893

    # j entry: from 0x0C back to 0x04 = -8
    # j(-8) = 0xFF9FF06F

    # sum_func at 0x10:
    # addi sp, sp, -8 = addi x2, x2, -8
    # -8 = 0xFF8 in 12-bit
    # (0xFF8 << 20) | (2<<15) | (0<<12) | (2<<7) | 0x13 = 0xFF810113

    # sw ra, 4(sp) = S-type: imm=4, rs2=x1, rs1=x2, funct3=010
    # imm[11:5]=0, imm[4:0]=4
    # (0<<25) | (1<<20) | (2<<15) | (2<<12) | (4<<7) | 0x23 = 0x00112223

    # sw a0, 0(sp) = S-type: imm=0, rs2=x10, rs1=x2
    # (0<<25) | (10<<20) | (2<<15) | (2<<12) | (0<<7) | 0x23 = 0x00A12023

    # bne a0, x0, +12: B-type, rs1=x10, rs2=x0, funct3=001, offset=+12
    # offset=12: bit12=0, bit10:5=000000, bit4:1=0110, bit11=0
    # (0<<31) | (0<<25) | (0<<20) | (10<<15) | (1<<12) | (6<<8) | (0<<7) | 0x63
    # = 0x00051663

    # addi sp, sp, 8 = (8<<20) | (2<<15) | (0<<12) | (2<<7) | 0x13 = 0x00810113

    # ret = jalr x0, x1, 0 = (0<<20) | (1<<15) | (0<<12) | (0<<7) | 0x67 = 0x00008067

    # recurse at 0x24:
    # addi a0, a0, -1 = (0xFFF<<20) | (10<<15) | (0<<12) | (10<<7) | 0x13 = 0xFFF50513

    # jal ra, sum_func: from 0x28 to 0x10 = -24
    # jal x1, -24:
    #   -24 = 0x1FFFE8 (21 bits)
    #   bit20=1, bits10:1=1111110100, bit11=1, bits19:12=11111111
    #   Hmm, let me use the python function:
    #   j(-24) with rd=1 instead of rd=0
    #   (1<<31) | (0b1111110100 << 21) | (1<<20) | (0xFF<<12) | (1<<7) | 0x6F
    #   = 0xFE9FF0EF

    # lw t0, 0(sp) = I-type load: imm=0, rs1=x2, funct3=010, rd=x5
    # (0<<20) | (2<<15) | (2<<12) | (5<<7) | 0x03 = 0x00012283

    # add a0, t0, a0 = R-type: rd=x10, rs1=x5, rs2=x10
    # (0<<25) | (10<<20) | (5<<15) | (0<<12) | (10<<7) | 0x33 = 0x00A28533

    # lw ra, 4(sp) = (4<<20) | (2<<15) | (2<<12) | (1<<7) | 0x03 = 0x00412083

    # addi sp, sp, 8 = 0x00810113 (same as before)

    # ret = 0x00008067

    core0_program = [
        0x7F000113,  # [0x00] addi x2, x0, 0x7F0     li sp, 0x7F0
        # entry (0x04):
        0x00080513,  # [0x04] addi x10, x16, 0         mv a0, x16 (read FIFO0)
        0x008000EF,  # [0x08] jal x1, +8                jal ra, sum_func (→0x10)
        0x00050893,  # [0x0C] addi x17, x10, 0          mv x17, a0 (push FIFO1)
        0xFF9FF06F,  # [0x10... wait, this is wrong addr]
    ]

    # Let me redo with correct addresses:
    core0_program = [
        0x7F000113,  # [0x00] li sp, 0x7F0
        # entry:
        0x00080513,  # [0x04] mv a0, x16           (read FIFO0, blocks)
        0x00C000EF,  # [0x08] jal ra, +12          (call sum_func at 0x14)
        0x00050893,  # [0x0C] mv x17, a0           (push result to FIFO1)
        0xFF9FF06F,  # [0x10] j -8                 (back to entry at 0x04)
        # sum_func (0x14):
        0xFF810113,  # [0x14] addi sp, sp, -8
        0x00112223,  # [0x18] sw ra, 4(sp)
        0x00A12023,  # [0x1C] sw a0, 0(sp)
        0x00051663,  # [0x20] bne a0, x0, +12      (→ recurse at 0x2C)
        0x00810113,  # [0x24] addi sp, sp, 8
        0x00008067,  # [0x28] ret                   (return 0)
        # recurse (0x2C):
        0xFFF50513,  # [0x2C] addi a0, a0, -1
        0xFE9FF0EF,  # [0x30] jal ra, -24          (call sum_func at 0x14)
        0x00012283,  # [0x34] lw t0, 0(sp)
        0x00A28533,  # [0x38] add a0, t0, a0
        0x00412083,  # [0x3C] lw ra, 4(sp)
        0x00810113,  # [0x40] addi sp, sp, 8
        0x00008067,  # [0x44] ret
    ]

    # Core 1: same recursive sum but reads FIFO1, pushes to FIFO3
    core1_program = [
        0x7F000113,  # [0x00] li sp, 0x7F0
        # entry:
        0x00088513,  # [0x04] mv a0, x17           (read FIFO1, blocks)
        0x00C000EF,  # [0x08] jal ra, +12          (call sum_func at 0x14)
        0x00050993,  # [0x0C] mv x19, a0           (push result to FIFO3)
        0xFF9FF06F,  # [0x10] j -8                 (back to entry at 0x04)
        # sum_func (0x14) — identical to core0:
        0xFF810113,  # [0x14] addi sp, sp, -8
        0x00112223,  # [0x18] sw ra, 4(sp)
        0x00A12023,  # [0x1C] sw a0, 0(sp)
        0x00051663,  # [0x20] bne a0, x0, +12
        0x00810113,  # [0x24] addi sp, sp, 8
        0x00008067,  # [0x28] ret
        # recurse (0x2C):
        0xFFF50513,  # [0x2C] addi a0, a0, -1
        0xFE9FF0EF,  # [0x30] jal ra, -24
        0x00012283,  # [0x34] lw t0, 0(sp)
        0x00A28533,  # [0x38] add a0, t0, a0
        0x00412083,  # [0x3C] lw ra, 4(sp)
        0x00810113,  # [0x40] addi sp, sp, 8
        0x00008067,  # [0x44] ret
    ]

    # Load core 0
    dut._log.info("Loading core 0 (sum → FIFO1)...")
    await spi.bio_write(0xE0, 0x00000000)
    for addr, instr in enumerate(core0_program):
        await spi.bio_imem_write(addr, instr)

    # Load core 1
    dut._log.info("Loading core 1 (sum → FIFO3)...")
    await spi.bio_write(0xE0, 0x00000001)
    for addr, instr in enumerate(core1_program):
        await spi.bio_imem_write(addr, instr)

    # Set quantum dividers (don't matter — no quantum waits in this program)
    await spi.bio_write(0x50, 0x100000)
    await spi.bio_write(0x54, 0x058000)

    # Enable both cores
    dut._log.info("Enabling both cores...")
    await spi.bio_write(0x00, 0x0000000F)

    # Test case 1: push 3
    # Expected: test_sum(test_sum(3)) = test_sum(6) = 21
    input_val = 3
    expected = test_sum(test_sum(input_val))
    dut._log.info(f"Pushing {input_val}...")
    await spi.bio_write(0x10, input_val)

    # Wait for computation (recursive, may take many cycles)
    await ClockCycles(dut.clk, 10000)

    # Check FIFO3
    flevel = await spi.bio_read(0x0C)
    fifo3_level = (flevel >> 12) & 0xF
    dut._log.info(f"FIFO3 level: {fifo3_level}")
    assert fifo3_level > 0, "FIFO3 empty — computation didn't complete"

    result = await spi.bio_read(0x2C)  # RXF3
    dut._log.info(f"  test_sum(test_sum({input_val})) = {result} (expected {expected})")
    assert result == expected, f"Got {result}, expected {expected}"

    # Test case 2: push 5
    input_val = 5
    expected = test_sum(test_sum(input_val))
    dut._log.info(f"Pushing {input_val}...")
    await spi.bio_write(0x10, input_val)
    await ClockCycles(dut.clk, 50000)

    flevel = await spi.bio_read(0x0C)
    fifo3_level = (flevel >> 12) & 0xF
    assert fifo3_level > 0, "FIFO3 empty for input 5"
    result = await spi.bio_read(0x2C)
    dut._log.info(f"  test_sum(test_sum({input_val})) = {result} (expected {expected})")
    assert result == expected, f"Got {result}, expected {expected}"

    # Test case 3: push 4
    input_val = 4
    expected = test_sum(test_sum(input_val))
    dut._log.info(f"Pushing {input_val}...")
    await spi.bio_write(0x10, input_val)
    await ClockCycles(dut.clk, 30000)

    flevel = await spi.bio_read(0x0C)
    fifo3_level = (flevel >> 12) & 0xF
    assert fifo3_level > 0, "FIFO3 empty for input 4"
    result = await spi.bio_read(0x2C)
    dut._log.info(f"  test_sum(test_sum({input_val})) = {result} (expected {expected})")
    assert result == expected, f"Got {result}, expected {expected}"

    await spi.bio_write(0x00, 0x00000000)

    dut._log.info("")
    dut._log.info("========================================")
    dut._log.info("  PASS — Stack test works!")
    dut._log.info("  Recursive function calls verified")
    dut._log.info("  sw/lw (stack memory) works")
    dut._log.info("  jal/ret (function calls) works")
    dut._log.info("  bne (conditional branch) works")
    dut._log.info("  add/addi (arithmetic) works")
    dut._log.info("  2-core cascaded computation works")
    dut._log.info("========================================")
