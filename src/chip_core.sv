// BIO-lite chip_core for wafer.space GF180MCU tapeout
// Connects the pad ring (chip_top.sv) to the BIO-lite design

`default_nettype none

module chip_core #(
    parameter NUM_INPUT_PADS  = 4,
    parameter NUM_BIDIR_PADS  = 44,
    parameter NUM_ANALOG_PADS = 6
)(
    `ifdef USE_POWER_PINS
    inout wire VDD,
    inout wire VSS,
    `endif

    input  wire clk,
    input  wire rst_n,

    // Input pads
    input  wire [NUM_INPUT_PADS-1:0]  input_in,
    output wire [NUM_INPUT_PADS-1:0]  input_pu,
    output wire [NUM_INPUT_PADS-1:0]  input_pd,

    // Bidirectional pads
    input  wire [NUM_BIDIR_PADS-1:0]  bidir_in,
    output wire [NUM_BIDIR_PADS-1:0]  bidir_out,
    output wire [NUM_BIDIR_PADS-1:0]  bidir_oe,
    output wire [NUM_BIDIR_PADS-1:0]  bidir_cs,
    output wire [NUM_BIDIR_PADS-1:0]  bidir_sl,
    output wire [NUM_BIDIR_PADS-1:0]  bidir_ie,
    output wire [NUM_BIDIR_PADS-1:0]  bidir_pu,
    output wire [NUM_BIDIR_PADS-1:0]  bidir_pd,

    // Analog pads (unused)
    inout  wire [NUM_ANALOG_PADS-1:0] analog
);

    // =========================================================
    // Input pad mapping
    // =========================================================
    // input_PAD[0] = spi_sclk
    // input_PAD[1] = spi_mosi
    // input_PAD[2] = spi_cs_n
    // input_PAD[3] = spare (active low pull-up as test point)

    wire spi_sclk = input_in[0];
    wire spi_mosi = input_in[1];
    wire spi_cs_n = input_in[2];

    // No pull-up/down on input pads
    assign input_pu = {NUM_INPUT_PADS{1'b0}};
    assign input_pd = {NUM_INPUT_PADS{1'b0}};

    // =========================================================
    // Bidirectional pad mapping
    // =========================================================
    // bidir[0]     = spi_miso (output only)
    // bidir[1]     = irq[0] (output only)
    // bidir[2]     = irq[1] (output only)
    // bidir[3..18] = gpio[0..15] (bidirectional)
    // bidir[19..43]= unused (tied off)

    wire        spi_miso;
    wire [1:0]  irq;
    wire [15:0] gpio_out;
    wire [15:0] gpio_dir;
    wire [15:0] gpio_in;

    // SPI MISO — output only
    assign bidir_out[0] = spi_miso;
    assign bidir_oe[0]  = 1'b1;

    // IRQ — output only
    assign bidir_out[1] = irq[0];
    assign bidir_oe[1]  = 1'b1;
    assign bidir_out[2] = irq[1];
    assign bidir_oe[2]  = 1'b1;

    assign irq[1] = 0;

    // GPIO — bidirectional
    assign bidir_out[18:3] = gpio_out;
    assign bidir_oe[18:3]  = gpio_dir;
    assign gpio_in         = bidir_in[18:3];

    // Unused pads — tied off
    assign bidir_out[NUM_BIDIR_PADS-1:19] = {(NUM_BIDIR_PADS-19){1'b0}};
    assign bidir_oe[NUM_BIDIR_PADS-1:19]  = {(NUM_BIDIR_PADS-19){1'b0}};

    // Pad configuration (all bidir pads)
    assign bidir_cs = {NUM_BIDIR_PADS{1'b0}};  // CMOS buffer mode
    assign bidir_sl = {NUM_BIDIR_PADS{1'b0}};  // Fast slew
    assign bidir_ie = ~bidir_oe;                 // Input enable when not outputting
    assign bidir_pu = {NUM_BIDIR_PADS{1'b0}};  // No pull-up
    assign bidir_pd = {NUM_BIDIR_PADS{1'b0}};  // No pull-down

    // =========================================================
    // BIO-lite instantiation
    // =========================================================

    bio_lite_top u_bio_lite (
        .clk       (clk),
        .reset_n   (rst_n),
        .spi_cs_n  (spi_cs_n),
        .spi_sclk  (spi_sclk),
        .spi_mosi  (spi_mosi),
        .spi_miso  (spi_miso),
        .gpio_in   (gpio_in),
        .gpio_out  (gpio_out),
        .gpio_dir  (gpio_dir),
        .irq       (irq[0])
    );

endmodule

`default_nettype wire
