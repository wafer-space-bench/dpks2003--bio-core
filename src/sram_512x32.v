// SRAM 512x32 wrapper for BIO-lite
//
// For simulation (default): behavioral reg array (identical to current design)
// For ASIC (`define USE_GF180_SRAM): 4× gf180mcu_fd_ip_sram__sram512x8m8wm1
//
// Interface matches the existing bio_lite.sv signals exactly:
//   Single-port, synchronous read, synchronous write with byte masks.
//   Read data appears one cycle after address is presented.

module sram_512x32 (
    input  wire        clk,
    input  wire        reset_n, 
    input  wire [8:0]  addr,       // 9-bit address (512 words)
    input  wire        wr_en,      // write enable (active high)
    input  wire [3:0]  wr_mask,    // byte write mask (active high, per byte)
    input  wire [31:0] wr_data,    // write data
    output wire [31:0] rd_data     // read data (registered, 1-cycle latency)
);

`ifdef USE_GF180_SRAM

    // =========================================================
    // ASIC mode: 4× GF180MCU SRAM macros (512×8 each)
    // =========================================================
    //
    // GF180 SRAM signals are ACTIVE LOW:
    //   CEN  = 0 → chip enabled
    //   GWEN = 0 → write mode, GWEN = 1 → read mode
    //   WEN  = 0 per bit → that bit gets written
    //
    // Our signals are ACTIVE HIGH, so we invert at the boundary.

    wire        cen  = ~reset_n;               // always enabled
    wire        gwen = ~wr_en;             // invert: our wr_en=1 → GWEN=0 (write)
    wire [7:0]  wen0 = wr_mask[0] ? 8'h00 : 8'hFF;  // invert: mask=1 → WEN=0 (write)
    wire [7:0]  wen1 = wr_mask[1] ? 8'h00 : 8'hFF;
    wire [7:0]  wen2 = wr_mask[2] ? 8'h00 : 8'hFF;
    wire [7:0]  wen3 = wr_mask[3] ? 8'h00 : 8'hFF;

    wire [7:0] q0, q1, q2, q3;

    gf180mcu_fd_ip_sram__sram512x8m8wm1 sram_byte0 (
        .CLK  (clk),
        .CEN  (cen),
        .GWEN (gwen),
        .WEN  (wen0),
        .A    (addr),
        .D    (wr_data[7:0]),
        .Q    (q0)
    );

    gf180mcu_fd_ip_sram__sram512x8m8wm1 sram_byte1 (
        .CLK  (clk),
        .CEN  (cen),
        .GWEN (gwen),
        .WEN  (wen1),
        .A    (addr),
        .D    (wr_data[15:8]),
        .Q    (q1)
    );

    gf180mcu_fd_ip_sram__sram512x8m8wm1 sram_byte2 (
        .CLK  (clk),
        .CEN  (cen),
        .GWEN (gwen),
        .WEN  (wen2),
        .A    (addr),
        .D    (wr_data[23:16]),
        .Q    (q2)
    );

    gf180mcu_fd_ip_sram__sram512x8m8wm1 sram_byte3 (
        .CLK  (clk),
        .CEN  (cen),
        .GWEN (gwen),
        .WEN  (wen3),
        .A    (addr),
        .D    (wr_data[31:24]),
        .Q    (q3)
    );

    // Registered read output (SRAM Q is already registered internally)
    
    assign rd_data = {q3, q2, q1, q0};
    

`else

    // =========================================================
    // Simulation mode: behavioral reg array
    // =========================================================
    reg [31:0] mem [0:511];

    always @(posedge clk) begin
        if (wr_en) begin
            if (wr_mask[0]) mem[addr][7:0]   <= wr_data[7:0];
            if (wr_mask[1]) mem[addr][15:8]  <= wr_data[15:8];
            if (wr_mask[2]) mem[addr][23:16] <= wr_data[23:16];
            if (wr_mask[3]) mem[addr][31:24] <= wr_data[31:24];
        end
        rd_data <= mem[addr];
    end

`endif

endmodule
