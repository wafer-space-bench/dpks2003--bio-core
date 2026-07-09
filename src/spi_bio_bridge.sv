// SPI-to-BIO Bridge (Verilog-2005)
//
// Wraps the spi_target module with a frame decoder.
// Collects 6 bytes per SPI transaction and produces host register/IMEM
// access signals for bio_lite.
//
// Frame format (48 bits = 6 bytes, MSB first):
//   Byte 0: Command (0x02=REG_WRITE, 0x03=REG_READ, 0x04=IMEM_WRITE, 0x05=IMEM_READ)
//   Byte 1: Address (register addr or IMEM word addr[7:0])
//   Bytes 2-5: Data (32-bit, MSB first)
//
// For reads: MISO returns data during bytes 2-5.

module spi_bio_bridge (
    input  wire        clk,
    input  wire        reset_n,

    // SPI pins (directly to chip pads)
    input  wire        spi_cs_n,
    input  wire        spi_sclk,
    input  wire        spi_mosi,
    output wire        spi_miso,
    output wire        spi_miso_oe,

    // Host register interface -> bio_lite
    output reg  [7:0]  host_addr,
    output reg  [31:0] host_wdata,
    input  wire [31:0] host_rdata,
    output reg         host_wr,
    output reg         host_rd,

    // IMEM access -> bio_lite
    output reg  [9:0]  host_imem_addr,
    output reg  [31:0] host_imem_wdata,
    input  wire [31:0] host_imem_rdata,
    output reg         host_imem_wr,
    output reg         host_imem_rd
);

    // =====================================================================
    // spi_target instance (8-bit, Mode 0)
    // =====================================================================
    wire [7:0] spi_rx_data;
    wire       spi_rx_valid;      // level signal — HIGH for one SPI clock period
    wire       spi_tx_hold;       // pulses at byte boundary — reload TX data now
    reg  [7:0] spi_tx_data;

    spi_target #(
        .CPOL  (1'b0),
        .CPHA  (1'b0),
        .WIDTH (8),
        .LSB   (1'b0)
    ) u_spi (
        .i_clk           (clk),
        .i_rst_n         (reset_n),
        .i_enable        (1'b1),
        .i_ss_n          (spi_cs_n),
        .i_sck           (spi_sclk),
        .i_mosi          (spi_mosi),
        .o_miso          (spi_miso),
        .o_miso_oe       (spi_miso_oe),
        .o_rx_data       (spi_rx_data),
        .o_rx_data_valid (spi_rx_valid),
        .i_tx_data       (spi_tx_data),
        .o_tx_data_hold  (spi_tx_hold)
    );

    // =====================================================================
    // Edge detector for rx_valid
    // spi_target's o_rx_data_valid is a LEVEL signal (held HIGH for many
    // system clocks). Our state machine needs a single-cycle PULSE.
    // =====================================================================
    reg rx_valid_prev;
    wire rx_valid_pulse = spi_rx_valid & ~rx_valid_prev;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n)
            rx_valid_prev <= 1'b0;
        else
            rx_valid_prev <= spi_rx_valid;
    end

    // =====================================================================
    // CS synchronizer for transaction boundary detection
    // =====================================================================
    reg [2:0] cs_sync;
    wire cs_active = ~cs_sync[2];

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n)
            cs_sync <= 3'b111;
        else
            cs_sync <= {cs_sync[1:0], spi_cs_n};
    end

    // =====================================================================
    // Frame decoder state machine
    // =====================================================================
    localparam [2:0] ST_CMD   = 3'd0;
    localparam [2:0] ST_ADDR  = 3'd1;
    localparam [2:0] ST_DATA0 = 3'd2;
    localparam [2:0] ST_DATA1 = 3'd3;
    localparam [2:0] ST_DATA2 = 3'd4;
    localparam [2:0] ST_DATA3 = 3'd5;

    reg [2:0]  state;
    reg [7:0]  cmd_reg;
    reg [8:0]  addr_reg;  // 9 bit to address the 512 word 
    reg [31:0] wdata_reg;

    // =====================================================================
    // Read data latch
    // Captures host_rdata on the same clock edge that host_rd fires.
    // host_rdata is combinational from bio_lite's read mux, so it's
    // valid the same cycle host_rd asserts. The latch holds the value
    // stable for hundreds of cycles while SPI shifts it out on MISO.
    // =====================================================================
reg [31:0] rdata_latch;
reg        imem_rd_pending;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        rdata_latch     <= 32'h0;
        imem_rd_pending <= 1'b0;
    end else begin
        if (host_rd)
            rdata_latch <= host_rdata;
        else if (host_imem_rd)
            imem_rd_pending <= 1'b1;
        else if (imem_rd_pending) begin
            rdata_latch     <= host_imem_rdata;
            imem_rd_pending <= 1'b0;
        end
    end
end

    // =====================================================================
    // Main state machine
    // =====================================================================
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state           <= ST_CMD;
            cmd_reg         <= 8'h0;
            addr_reg        <= 9'h0;
            wdata_reg       <= 32'h0;
            host_wr         <= 1'b0;
            host_rd         <= 1'b0;
            host_imem_wr    <= 1'b0;
            host_imem_rd    <= 1'b0;
            host_addr       <= 8'h0;
            host_wdata      <= 32'h0;
            host_imem_addr  <= 10'h0;
            host_imem_wdata <= 32'h0;
        end else begin
            // Clear single-cycle pulses every cycle
            host_wr      <= 1'b0;
            host_rd      <= 1'b0;
            host_imem_wr <= 1'b0;
            host_imem_rd <= 1'b0;

            // Reset state on CS deassert
            if (!cs_active) begin
                state <= ST_CMD;
            end else if (rx_valid_pulse) begin
                case (state)
                    ST_CMD: begin
                        cmd_reg <= spi_rx_data;
                        state   <= ST_ADDR;
                    end

                    ST_ADDR: begin
                        // IMEM cmds: capture full 9-bit start address
                        // (cmd[1] = addr[8]); others: plain 8-bit reg addr
                        if (cmd_reg == 8'h04 || cmd_reg == 8'h05 ||
                            cmd_reg == 8'h06 || cmd_reg == 8'h07)
                            addr_reg <= {cmd_reg[1], spi_rx_data};
                        else
                            addr_reg <= {1'b0, spi_rx_data};

                        if (cmd_reg == 8'h03) begin
                            host_addr <= spi_rx_data;
                            host_rd   <= 1'b1;
                        end else if (cmd_reg == 8'h05 || cmd_reg == 8'h07) begin
                            host_imem_addr <= {1'b0, cmd_reg[1], spi_rx_data};
                            host_imem_rd   <= 1'b1;
                        end
                        state <= ST_DATA0;
                    end

                    ST_DATA0: begin
                        wdata_reg[31:24] <= spi_rx_data;
                        state <= ST_DATA1;
                    end

                    ST_DATA1: begin
                        wdata_reg[23:16] <= spi_rx_data;
                        state <= ST_DATA2;
                    end

                    ST_DATA2: begin
                        wdata_reg[15:8] <= spi_rx_data;
                        state <= ST_DATA3;
                    end

                    ST_DATA3: begin
                        wdata_reg[7:0] <= spi_rx_data;
                        if (cmd_reg == 8'h02) begin
                            host_addr  <= addr_reg[7:0];
                            host_wdata <= {wdata_reg[31:8], spi_rx_data};
                            host_wr    <= 1'b1;
                            state      <= ST_CMD;
                        end else if (cmd_reg == 8'h04 || cmd_reg == 8'h06) begin
                            host_imem_addr  <= {1'b0, addr_reg};  // full 9 bits
                            host_imem_wdata <= {wdata_reg[31:8], spi_rx_data};
                            host_imem_wr    <= 1'b1;
                            addr_reg        <= addr_reg + 9'd1;   // burst support
                            state           <= ST_DATA0;          // stay in data phase
                        end else begin
                            state <= ST_CMD;
                        end
                    end

                    default: state <= ST_CMD;
                endcase
            end
        end
    end

    // =====================================================================
    // TX data mux — feed read data byte-by-byte on MISO
    //
    // spi_target grabs i_tx_data when o_tx_data_hold fires (at each byte
    // boundary). At that moment, the state reflects which byte JUST finished
    // receiving, so the NEXT byte to transmit is:
    //
    //   State when tx_hold fires    Byte being shifted out    Data to present
    //   ST_CMD                      byte 0 (cmd)              don't care (0x00)
    //   ST_ADDR                     byte 1 (addr)             rdata_latch[31:24]
    //   ST_DATA0                    byte 2 (data MSB)         rdata_latch[23:16]
    //   ST_DATA1                    byte 3                    rdata_latch[15:8]
    //   ST_DATA2                    byte 4                    rdata_latch[7:0]
    //   ST_DATA3                    byte 5 (data LSB)         don't care (0x00)
    //
    // tx_hold fires BETWEEN bytes (on the opposite SPI clock edge after the
    // last bit). At this moment, state has already advanced from the
    // rx_valid_pulse that fired on the sampling edge. So the state matches
    // "which byte is about to shift out."
    //
    // BUT: there's a subtlety. tx_hold fires on w_sck_edge_op (falling edge
    // for Mode 0), and rx_valid_pulse fires on w_sck_edge (rising edge).
    // The rising edge comes first in Mode 0, so:
    //   1. Rising edge: last bit sampled, count wraps 7→0, rx_valid goes HIGH
    //   2. System clock: rx_valid_pulse detected, state advances
    //   3. Falling edge: tx_hold fires, count==0, w_sck_edge_op is true
    //      At this point state has ALREADY advanced → correct mapping
    //
    // However, for the FIRST byte after CS falls, tx_hold fires from the
    // CS falling edge path, when state is still ST_CMD → default → 0x00.
    // This is correct because byte 0 MISO is don't care.
    // =====================================================================
always @(*) begin
        case (state)
            ST_DATA0: spi_tx_data = rdata_latch[31:24];
            ST_DATA1: spi_tx_data = rdata_latch[23:16];
            ST_DATA2: spi_tx_data = rdata_latch[15:8];
            ST_DATA3: spi_tx_data = rdata_latch[7:0];
            default:  spi_tx_data = 8'h00;
        endcase
    end

endmodule