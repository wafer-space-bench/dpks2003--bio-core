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
    $(PWD)/../../src/sram_512x32.v

MODULE ?= test_chip_id

EXTRA_ARGS += --trace --trace-structs
EXTRA_ARGS += -Wno-fatal

include $(shell cocotb-config --makefiles)/Makefile.sim
