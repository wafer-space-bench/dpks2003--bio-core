# BIO-Coprocessor — Cocotb Test Suite

## Setup

```bash
# Enter nix shell
cd <repoRoot> && nix-shell


# Navigate to test directory
cd sim/cocotb
```

## Running Tests

```bash
# Run a single test
MODULE=test_chip_id make clean && make

# Run all core tests
for m in test_chip_id test_bunnie test_imem_fifo test_spi_bitbang \
         test_events test_gpio_input test_multiverse \
         test_gpio_quantum_twocore testF_regression test_gpio_32; do
    echo "=== $m ===" && MODULE=$m make clean && MODULE=$m make 2>&1 | tail -5
done
```




## Flows

| Flow | Command | What it uses | Catches |
|------|---------|--------------|---------|
| **Behavioural** | `make MODULE=<test>` | Verilator, behavioural SRAM/ICG | Fast functional regression |
| **Real SRAM** | `make -f Makefile.gf MODULE=<test>` | Verilator + PDK SRAM model | Macro latency, CEN handshake |
| **4-state** | `make -f Makefile.ic MODULE=<test>` | Icarus + PDK SRAM, ICG, primitives | X propagation, unreset flops |
| **Gate-level** | see below | Icarus + synthesised netlist | Synthesis/CTS-level bugs |

All RTL flows run against `bio_lite_top`. GL runs against `chip_top` (pads).


# Gate Level Simulation 

```
NL=../../librelane/runs/RUN_/06-yosys-synthesis/chip_top.nl.v

# the 5-test ladder (smoke: pads, SPI, IMEM, execution, IRQ)
GL=1 POWER=0 PDK_ROOT=../../gf180mcu NETLIST=$NL python3 chip_top_tb.py

# extended suite (stall flavours, dual-core, restart policy, GPIO input)
GL=1 POWER=0 PDK_ROOT=../../gf180mcu NETLIST=$NL \
  TEST_MODULE=chip_top_gl_ext python3 chip_top_tb.py

# bootstrap corners + mul
GL=1 POWER=0 PDK_ROOT=../../gf180mcu NETLIST=$NL \
  TEST_MODULE=test_bootstrap_corners_gl python3 chip_top_tb.py

# stall races
GL=1 POWER=0 PDK_ROOT=../../gf180mcu NETLIST=$NL \
  TEST_MODULE=test_stall_races_gl python3 chip_top_tb.py

# adversarial / X-hunting
GL=1 POWER=0 PDK_ROOT=../../gf180mcu NETLIST=$NL \
  TEST_MODULE=chip_top_gl_rigor python3 chip_top_tb.py
```

**A single test:**
```bash
... TEST_MODULE=chip_top_gl_ext TESTCASE=gl_core1_solo python3 chip_top_tb.py

```

## Test Files

### Core Functionality

| Test | Module | Description | Status |
|------|--------|-------------|--------|
| Chip ID | `test_chip_id` | SPI read of ID register 0xFC → expects `0x0B100002` | ✅ PASS |
| IMEM + FIFO | `test_imem_fifo` | Write program via SPI, core echoes FIFO data to GPIO | ✅ PASS |
| bunnie Hello | `test_bunnie` | bunnie's walking-1 GPIO test from Betrusted | ✅ PASS |
| SPI Bitbang | `test_spi_bitbang` | Core bit-bangs SPI master on GPIO[2:0], 8 patterns | ✅ PASS |
| Events | `test_events` | FIFO level events (eq/lt/gt) + software events | ✅ PASS |
| GPIO Input | `test_gpio_input` | 10 input patterns, sync bypass, loopback | ✅ PASS |

### Multi-Core

| Test | Module | Description | Status |
|------|--------|-------------|--------|
| Multiverse | `test_multiverse` | 2-core GPIO counters with snap-to-quantum | ✅ PASS |
| Quantum Twocore | `test_gpio_quantum_twocore` | Dual-core GPIO output with quantum sync | ✅ PASS |

### Regression

| Test | Module | Description | Status |
|------|--------|-------------|--------|
| Full Regression | `testF_regression` | 7 tests: register defaults, readback, FIFO edge cases, FIFO blocking, core restart, GPIO inversion, reset behavior | ✅ PASS |
| 32-bit GPIO | `test_gpio_32` | 6 tests: upper output, upper input, walking-1 32-bit, direction, inversion, cross-side | ✅ PASS |

### Known Limitations

| Test | Module | Description | Status |
|------|--------|-------------|--------|
| Clock Counter | `test_aclk` | Delta = 4 not 3 (REGISTER_RAM adds 1 cycle) | ⚠️ KNOWN |
| Stack | `test_stack` | FIFO3 empty — needs more cycles or encoding fix | ⚠️ KNOWN |
| GPIO Inversion | `test_gpio_input` | Inversion sub-test — only passes after XOR fix | ⚠️ KNOWN |

## Test Architecture

```
┌──────────────────┐         ┌──────────────────┐
│  Python cocotb   │  SPI    │   bio_lite_top   │
│  test_*.py       │◄───────►│   (Verilog DUT)  │
│                  │  GPIO   │                  │
│  spi_master.py   │◄───────►│   PicoRV32 × 2  │
│  (SPI bit-bang)  │         │   SRAM, FIFOs    │
└──────────────────┘         └──────────────────┘
        │                            │
        ▼                            ▼
   Verilator                   RTL simulation
   (4-state sim)               (no SDF timing)
```

## BIO Register Quick Reference

| Register | Address | Access | Description |
|----------|---------|--------|-------------|
| x16–x19 | — | Core R | FIFO read (blocks on empty) |
| x20 | — | Core W | Quantum wait (end of time slice) |
| x21 | — | Core W | GPIO write (through mask) |
| x22 | — | Core W | GPIO set (OR bits) |
| x23 | — | Core W | GPIO clear (AND NOT bits) |
| x24 | — | Core W | GPIO direction set |
| x25 | — | Core W | GPIO direction clear |
| x26 | — | Core W | GPIO write mask |
| x31 | — | Core R | {core_id, clk_count} |


Requires: netlist from LibreLane, PDK liberty/verilog models, SRAM behavioral model.