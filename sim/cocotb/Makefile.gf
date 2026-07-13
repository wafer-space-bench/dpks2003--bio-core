SIM = verilator
TOPLEVEL_LANG = verilog
TOPLEVEL = bio_lite_top

VERILOG_SOURCES = \
    $(PWD)/../../src/bio_lite.sv \
    $(PWD)/../../src/spi_bio_bridge.sv \
    $(PWD)/../../src/bio_lite_top.sv \
    $(PWD)/../../src/spi_slave.v \
    $(PWD)/../../src/picorv32.v \
    $(PWD)/../../src/regfifo.v \
    $(PWD)/../../src/pio_divider.v \
    $(PWD)/../../src/sram_512x32.v \
    $(PWD)/sim/models/gf180mcu_fd_ip_sram__sram512x8m8wm1.v

EXTRA_ARGS += --trace --trace-structs --timing
EXTRA_ARGS += -Wno-fatal -Wno-SPECIFYIGN
EXTRA_ARGS += -DUSE_GF180_SRAM

MODULE ?= test_imem_full_range
include $(shell cocotb-config --makefiles)/Makefile.sim
