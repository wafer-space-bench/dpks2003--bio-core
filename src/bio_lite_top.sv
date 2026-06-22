// BIO-lite Top-Level
//
// Chip top for GF180MCU tapeout and FPGA validation.
// Instantiates SPI bridge + BIO-lite core.
// GPIO mirroring: 16 physical pins → 32 internal slots.

module bio_lite_top #(
    parameter NUM_MACH = 2,
    parameter NUM_GPIO = 16
)(
    input  wire        clk,
    input  wire        reset_n,

    // SPI slave interface (directly to chip pads)
    input  wire        spi_cs_n,
    input  wire        spi_sclk,
    input  wire        spi_mosi,
    output wire        spi_miso,

    // GPIO (physical pins)
    input  wire [NUM_GPIO-1:0] gpio_in,
    output wire [NUM_GPIO-1:0] gpio_out,
    output wire [NUM_GPIO-1:0] gpio_dir,

    // IRQ
    output wire [NUM_MACH-1:0] irq
);

    // Internal 32-bit GPIO with mirroring
    wire [31:0] gpio_in_full;
    wire [31:0] gpio_out_full;
    wire [31:0] gpio_dir_full;

    // Mirror: physical pins map to both lower and upper 16 slots
    // This enables bunnie's clocking hack — same signal, different sync/event settings
    assign gpio_in_full[NUM_GPIO-1:0]  = gpio_in;           // direct: slots 0–15
    assign gpio_in_full[31:NUM_GPIO]   = gpio_in;           // mirrored: slots 16–31

    // Output: only lower 16 bits drive physical pins
    assign gpio_out = gpio_out_full[NUM_GPIO-1:0];
    assign gpio_dir = gpio_dir_full[NUM_GPIO-1:0];

    // Host register interface wires (SPI bridge → BIO-lite)
    wire [7:0]  host_addr;
    wire [31:0] host_wdata;
    wire [31:0] host_rdata;
    wire        host_wr;
    wire        host_rd;

    // IMEM access wires (SPI bridge → BIO-lite)
    wire [0:0]  host_imem_core;
    wire [9:0]  host_imem_addr;
    wire [31:0] host_imem_wdata;
    wire [31:0] host_imem_rdata;
    wire        host_imem_wr;
    wire        host_imem_rd;

    // IMEM core select — directly from register decoder via host register interface
    // The spi_bio_bridge routes IMEM commands to the host_imem_* ports.
    // The IMEM_CORE_SEL register (0xE0) is inside bio_lite.sv's register decoder,
    // but host_imem_core needs to come from the bridge's perspective.
    // Solution: bio_lite exposes host_imem_core as an output driven by the 0xE0 register.
    // For now, we tie it through the register interface — the bridge sets it via REG_WRITE
    // to 0xE0 before issuing IMEM commands.
    reg [0:0] imem_core_sel;
    always @(posedge clk or negedge reset_n) begin
        if (~reset_n) imem_core_sel <= 0;
        else if (host_wr && host_addr == 8'hE0) imem_core_sel <= host_wdata[0];
    end
    assign host_imem_core = imem_core_sel;

    // =====================================================================
    // SPI Bridge (wraps your Shrike SPI_Slave)
    // =====================================================================
    spi_bio_bridge u_spi_bridge (
        .clk            (clk),
        .reset_n        (reset_n),
        // SPI pins
        .spi_cs_n       (spi_cs_n),
        .spi_sclk       (spi_sclk),
        .spi_mosi       (spi_mosi),
        .spi_miso       (spi_miso),
        // Host register interface
        .host_addr      (host_addr),
        .host_wdata     (host_wdata),
        .host_rdata     (host_rdata),
        .host_wr        (host_wr),
        .host_rd        (host_rd),
        // IMEM access
        .host_imem_addr (host_imem_addr),
        .host_imem_wdata(host_imem_wdata),
        .host_imem_rdata(host_imem_rdata),
        .host_imem_wr   (host_imem_wr),
        .host_imem_rd   (host_imem_rd)
    );

    // =====================================================================
    // BIO-lite Core
    // =====================================================================
    bio_lite #(
        .NUM_MACH       (NUM_MACH),
        .MEM_SIZE_BYTES (32'h0800)
    ) u_bio (
        .clk            (clk),
        .reset_n        (reset_n),
        // GPIO (32-bit internal)
        .gpio_in        (gpio_in_full),
        .gpio_out       (gpio_out_full),
        .gpio_dir       (gpio_dir_full),
        // IRQ
        .irq            (irq),
        // Host register interface
        .host_addr      (host_addr),
        .host_wdata     (host_wdata),
        .host_rdata     (host_rdata),
        .host_wr        (host_wr),
        .host_rd        (host_rd),
        // IMEM access
        .host_imem_core (host_imem_core),
        .host_imem_addr (host_imem_addr),
        .host_imem_wdata(host_imem_wdata),
        .host_imem_rdata(host_imem_rdata),
        .host_imem_wr   (host_imem_wr),
        .host_imem_rd   (host_imem_rd)
    );

endmodule
