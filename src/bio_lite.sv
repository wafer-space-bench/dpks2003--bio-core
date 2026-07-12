// BIO-lite: 2-core BIO programmable I/O coprocessor for GF180MCU
//
// Derived from baochip-1x/rtl/modules/bio_bdma/rtl/bio_bdma.sv
// (the verified, taped-out BIO design by bunnie Huang / CrossBar, Inc.)
//
// SPDX-FileCopyrightText: 2024 CrossBar, Inc.
// SPDX-FileCopyrightText: 2026 Deepak Sharda (BIO-lite modifications)
// SPDX-License-Identifier: CERN-OHL-W-2.0
//
// Modifications for BIO-lite:
//   - Reduced from 4 cores to 2 (NUM_MACH parameter)
//   - Single clock domain (removed pclk/aclk/hclk/dmaclk CDC)
//   - APB register bank → simple host register interface
//   - ioif → flat 32-bit GPIO (top-level mirrors 16 physical pins)
//   - Per-core Ram_1rw_s → per-core reg array (swap to OpenRAM for silicon)
//   - BDMA removed (DMA FSM, AXI/AHB bridge, address filters, aliases)
//   - REGISTER_RAM mode (registered memory interface — taped-out config)
//   - ENABLE_FAST_MUL enabled (taped-out config)
//   - FIFO reset includes core restart (bio_bdma.sv behavior)
//   - sync_bypass uses gpio_in_sync0 not raw (bio_bdma.sv behavior)
//   - extclk_selected uses gpio_in_sync1 always (bio_bdma.sv behavior)
//   - Event stalling: == 0 semantics (bio_bdma.sv behavior)
//   - IRQ reduced to 2 channels (matches core count)

module bio_lite #(
    parameter NUM_MACH = 2,
    parameter MEM_SIZE_BYTES = 32'h0800
)(
    input  logic        clk,
    input  logic        reset_n,

    // GPIO — 32-bit internal (top-level mirrors 16 physical pins to 32 slots)
    input  logic [31:0] gpio_in,
    output logic [31:0] gpio_out,
    output logic [31:0] gpio_dir,

    // IRQ — one per core
    output logic [NUM_MACH-1:0] irq,

    // Host register interface (from SPI bridge)
    input  logic [7:0]  host_addr,
    input  logic [31:0] host_wdata,
    output logic [31:0] host_rdata,
    input  logic        host_wr,
    input  logic        host_rd,

    // IMEM host access (active when target core is disabled)
    input  logic [0:0]  host_imem_core,   // which core's IMEM to access
    input  logic [9:0]  host_imem_addr,   // word address within that core's IMEM
    input  logic [31:0] host_imem_wdata,
    output logic [31:0] host_imem_rdata,
    input  logic        host_imem_wr,
    input  logic        host_imem_rd
);
    localparam MEM_SIZE_WORDS = MEM_SIZE_BYTES / 4;
    localparam MEM_ADDR_BITS  = $clog2(MEM_SIZE_WORDS);
    localparam PC_SIZE_BITS   = $clog2(MEM_SIZE_BYTES);

    // Module glue
    logic reset;
    logic resetn;
    assign reset  = ~reset_n;
    assign resetn = reset_n;

    // =====================================================================
    // Signal declarations
    // =====================================================================

    // GPIO
    logic [31:0] oe_invert, out_invert, in_invert;
    logic [31:0] gpio_in_cleaned, gpio_in_snapped, gpio_in_maybe_snapped;
    logic [31:0] gpio_in_sync0, gpio_in_sync1;
    logic [31:0] gpio_out_int, gpio_dir_int;
    logic [31:0] sync_bypass;
    logic [31:0] irqmask [NUM_MACH];
    logic [NUM_MACH-1:0] irq_agg, irq_agg_q, irq_edge;
    logic snap_output_to_quantum;
    logic [0:0] snap_output_to_which;
    logic snap_input_to_quantum;
    logic [0:0] snap_input_to_which;

    // Machine
    logic [15:0] div_int     [NUM_MACH];
    logic [7:0]  div_frac    [NUM_MACH];
    logic [7:0]  unused_div  [NUM_MACH];
    logic [NUM_MACH-1:0] clkdiv_restart, restart, penable;
    logic [NUM_MACH-1:0] use_extclk, extclk_selected, extclk_selected_q, quantum;
    logic [4:0] extclk_gpio [NUM_MACH];
    logic [NUM_MACH-1:0] core_clk, stall, trap;

    // REGISTER_RAM mode memory interface
    logic [NUM_MACH-1:0] mem_valid, mem_instr, mem_ready;
    logic [31:0] mem_addr   [NUM_MACH];
    logic [31:0] mem_wdata  [NUM_MACH];
    logic [3:0]  mem_wstrb  [NUM_MACH];
    logic [31:0] mem_rdata  [NUM_MACH];
    // Look-ahead (still used for some control signals)
    logic [NUM_MACH-1:0] mem_la_read, mem_la_write;
    logic [31:0] mem_la_addr  [NUM_MACH];
    logic [31:0] mem_la_wdata [NUM_MACH];
    logic [3:0]  mem_la_wstrb [NUM_MACH];
    // REGISTER_RAM support
    reg [NUM_MACH-1:0] mem_valid_d, dead_cycle;

    // High register interfaces (BIO register file)
    logic [31:0] mach_regfifo_rdata [NUM_MACH];
    logic [3:0]  mach_regfifo_rd    [NUM_MACH];
    logic [31:0] mach_regfifo_wdata [NUM_MACH];
    logic [3:0]  mach_regfifo_wr    [NUM_MACH];
    logic [NUM_MACH-1:0] quanta_halt;
    logic [31:0] gpio_set  [NUM_MACH];
    logic [31:0] gpio_clr  [NUM_MACH];
    logic [31:0] gpdir_set [NUM_MACH];
    logic [31:0] gpdir_clr [NUM_MACH];
    logic [NUM_MACH-1:0] gpio_set_valid, gpio_clr_valid;
    logic [NUM_MACH-1:0] gpdir_set_valid, gpdir_clr_valid;
    logic [23:0] event_set [NUM_MACH];
    logic [NUM_MACH-1:0] event_set_valid;
    logic [23:0] event_clr [NUM_MACH];
    logic [NUM_MACH-1:0] event_clr_valid;
    logic [31:0] aggregated_events;
    logic [NUM_MACH-1:0] stalling_for_event;
    logic [23:0] host_event_set;
    logic        host_event_set_valid;
    logic [23:0] host_event_clr;
    logic        host_event_clr_valid;
    logic [29:0] core_clk_count [NUM_MACH];

    // Modes
    logic [NUM_MACH-1:0] en, en_sync, imem_wr_mode;

    // FIFOs (always 4, independent of core count)
    logic [31:0] regfifo_wdata [4];
    logic        regfifo_we    [4];
    logic        regfifo_writable [4];
    logic [31:0] regfifo_rdata [4];
    logic        regfifo_re    [4];
    logic        regfifo_readable [4];
    logic [3:0]  regfifo_level [4];
    logic [3:0]  fifo_event_level [8];
    logic [7:0]  fifo_event_eq_mask, fifo_event_lt_mask, fifo_event_gt_mask;
    logic [31:0] fdin  [4];
    logic [31:0] fdout [4];
    logic [3:0]  push, pull;
    logic [3:0]  fifo_to_reset;
    logic        do_fifo_clr;

    // Register bank signals
    logic ctl_action, ctl_action_q;

    // =====================================================================
    // HOST REGISTER DECODER
    // Replaces the entire APB SFR bank from bio_bdma.sv
    // =====================================================================

    // Latch FIFO read data
    always_ff @(posedge clk) begin
        fdout <= regfifo_rdata;
    end

    // Write decoder
    always_ff @(posedge clk or negedge reset_n) begin
        if (~reset_n) begin
            en <= '0; restart <= '0; clkdiv_restart <= '0;
            snap_output_to_quantum <= 0; snap_output_to_which <= '0;
            snap_input_to_quantum <= 0;  snap_input_to_which <= '0;
            fdin[0] <= '0; fdin[1] <= '0; fdin[2] <= '0; fdin[3] <= '0;
            push <= '0; pull <= '0;
            fifo_event_level[0] <= '0; fifo_event_level[1] <= '0;
            fifo_event_level[2] <= '0; fifo_event_level[3] <= '0;
            fifo_event_level[4] <= '0; fifo_event_level[5] <= '0;
            fifo_event_level[6] <= '0; fifo_event_level[7] <= '0;
            fifo_event_eq_mask <= '0; fifo_event_lt_mask <= '0; fifo_event_gt_mask <= '0;
            host_event_set <= '0; host_event_set_valid <= 0;
            host_event_clr <= '0; host_event_clr_valid <= 0;
            use_extclk <= '0;
            extclk_gpio[0] <= '0; extclk_gpio[1] <= '0;
            fifo_to_reset <= '0; do_fifo_clr <= 0;
            div_int[0] <= '0; div_frac[0] <= '0; unused_div[0] <= '0;
            div_int[1] <= '0; div_frac[1] <= '0; unused_div[1] <= '0;
            sync_bypass <= '0; oe_invert <= '0; out_invert <= '0; in_invert <= '0;
            irqmask[0] <= '0; irqmask[1] <= '0; irq_edge <= '0;
            ctl_action <= 0; ctl_action_q <= 0;
        end else begin
            // Clear single-cycle action pulses
            push <= '0; pull <= '0; ctl_action <= 0;
            host_event_set_valid <= 0; host_event_clr_valid <= 0; do_fifo_clr <= 0;

            if (host_wr) begin
                case (host_addr)
                    8'h00: begin
                        {clkdiv_restart, restart, en} <= host_wdata[NUM_MACH*3-1:0];
                        ctl_action <= 1;
                    end
                    8'h08: {snap_input_to_quantum, snap_input_to_which,
                            snap_output_to_quantum, snap_output_to_which} <= host_wdata[3:0];
                    8'h10: begin fdin[0] <= host_wdata; push[0] <= 1; end
                    8'h14: begin fdin[1] <= host_wdata; push[1] <= 1; end
                    8'h18: begin fdin[2] <= host_wdata; push[2] <= 1; end
                    8'h1C: begin fdin[3] <= host_wdata; push[3] <= 1; end
                    8'h30: {fifo_event_level[7], fifo_event_level[6],
                            fifo_event_level[5], fifo_event_level[4],
                            fifo_event_level[3], fifo_event_level[2],
                            fifo_event_level[1], fifo_event_level[0]} <= host_wdata;
                    8'h34: {fifo_event_gt_mask, fifo_event_eq_mask, fifo_event_lt_mask} <= host_wdata[23:0];
                    8'h38: begin host_event_set <= host_wdata[23:0]; host_event_set_valid <= 1; end
                    8'h3C: begin host_event_clr <= host_wdata[23:0]; host_event_clr_valid <= 1; end
                    8'h44: {extclk_gpio[1], extclk_gpio[0], use_extclk} <= host_wdata[NUM_MACH+9:0];
                    8'h48: begin fifo_to_reset <= host_wdata[3:0]; do_fifo_clr <= 1; end
                    8'h50: {div_int[0], div_frac[0], unused_div[0]} <= host_wdata;
                    8'h54: {div_int[1], div_frac[1], unused_div[1]} <= host_wdata;
                    8'h60: sync_bypass <= host_wdata;
                    8'h64: oe_invert   <= host_wdata;
                    8'h68: out_invert  <= host_wdata;
                    8'h6C: in_invert   <= host_wdata;
                    8'h70: irqmask[0]  <= host_wdata;
                    8'h74: irqmask[1]  <= host_wdata;
                    8'h80: irq_edge    <= host_wdata[NUM_MACH-1:0];
                    default: ;
                endcase
            end
            if (host_rd) begin
                case (host_addr)
                    8'h20: pull[0] <= 1;
                    8'h24: pull[1] <= 1;
                    8'h28: pull[2] <= 1;
                    8'h2C: pull[3] <= 1;
                    default: ;
                endcase
            end
            if (ctl_action_q) begin restart <= '0; clkdiv_restart <= '0; end
            ctl_action_q <= ctl_action;
        end
    end

    // Read mux (combinational)
    always_comb begin
        host_rdata = 32'h0;
        case (host_addr)
            8'h00: host_rdata = {{(32-NUM_MACH*3){1'b0}}, clkdiv_restart, restart, en};
            8'h04: host_rdata = {16'(MEM_SIZE_BYTES), 8'(NUM_MACH), 8'd8};
            8'h08: host_rdata = {28'h0, snap_input_to_quantum, snap_input_to_which,
                                 snap_output_to_quantum, snap_output_to_which};
            8'h0C: host_rdata = {regfifo_level[3], regfifo_level[2],
                                 regfifo_level[1], regfifo_level[0]};
            8'h20: host_rdata = fdout[0];
            8'h24: host_rdata = fdout[1];
            8'h28: host_rdata = fdout[2];
            8'h2C: host_rdata = fdout[3];
            8'h40: host_rdata = aggregated_events;
            8'h50: host_rdata = {div_int[0], div_frac[0], unused_div[0]};
            8'h54: host_rdata = {div_int[1], div_frac[1], unused_div[1]};
            8'h60: host_rdata = sync_bypass;
            8'h64: host_rdata = oe_invert;
            8'h68: host_rdata = out_invert;
            8'h6C: host_rdata = in_invert;
            8'h70: host_rdata = irqmask[0];
            8'h74: host_rdata = irqmask[1];
            8'h84: host_rdata = gpio_out;
            8'h88: host_rdata = gpio_dir;
            8'hF0: host_rdata = host_imem_rdata;
            8'hFC: host_rdata = 32'h0B10_0002;
            default: host_rdata = 32'h0;
        endcase
    end

    // =====================================================================
    // PER-CORE INSTRUCTION MEMORY + HOST MUX
    // Architecture from bio_bdma.sv: private single-port SRAM per core.
    // REGISTER_RAM mode (registered memory interface).
    // =====================================================================
    logic                    ram_wr_en   [NUM_MACH];
    logic [3:0]              ram_wr_mask [NUM_MACH];
    logic [MEM_ADDR_BITS-1:0] ram_addr   [NUM_MACH];
    logic [31:0]             ram_wr_data [NUM_MACH];
    logic [31:0]             ram_rd_data [NUM_MACH];

/*
    generate
        for (genvar j = 0; j < NUM_MACH; j = j + 1) begin: imem_block
            // Per-core private instruction memory
            // Reg array for simulation — swap to OpenRAM single-port SRAM for silicon
            reg [31:0] imem [0:MEM_SIZE_WORDS-1];

            // Single-port: shared address for read and write
            always_ff @(posedge clk) begin
                if (ram_wr_en[j]) begin
                    if (ram_wr_mask[j][0]) imem[ram_addr[j]][7:0]   <= ram_wr_data[j][7:0];
                    if (ram_wr_mask[j][1]) imem[ram_addr[j]][15:8]  <= ram_wr_data[j][15:8];
                    if (ram_wr_mask[j][2]) imem[ram_addr[j]][23:16] <= ram_wr_data[j][23:16];
                    if (ram_wr_mask[j][3]) imem[ram_addr[j]][31:24] <= ram_wr_data[j][31:24];
                end
                ram_rd_data[j] <= imem[ram_addr[j]];
            end
        end
    endgenerate

*/

 /*
    generate
        for (genvar j = 0; j < NUM_MACH; j = j + 1) begin: imem_block
            sram_512x32 u_imem (
                .clk     (clk),
                .addr    (ram_addr[j]),
                .wr_en   (ram_wr_en[j]),
                .wr_mask (ram_wr_mask[j]),
                .wr_data (ram_wr_data[j]),
                .rd_data (ram_rd_data[j])
            );
        end
    endgenerate
*/

// Core 0 IMEM
    sram_512x32 u_imem_core0 (
        .clk     (clk),
        .reset_n (reset_n),
        .addr    (ram_addr[0][8:0]),
        .wr_en   (ram_wr_en[0]),
        .wr_mask (ram_wr_mask[0]),
        .wr_data (ram_wr_data[0]),
        .rd_data (ram_rd_data[0])
    );

    // Core 1 IMEM
    sram_512x32 u_imem_core1 (
        .clk     (clk),
        .reset_n (reset_n),
        .addr    (ram_addr[1][8:0]),
        .wr_en   (ram_wr_en[1]),
        .wr_mask (ram_wr_mask[1]),
        .wr_data (ram_wr_data[1]),
        .rd_data (ram_rd_data[1])
    );


    // IMEM host readback — mux based on selected core
    assign host_imem_rdata = ram_rd_data[host_imem_core];
    assign mem_rdata = ram_rd_data;

    // Per-core host ↔ core mux (from bio_bdma.sv lines 1738–1763)
    generate
        for (genvar j = 0; j < NUM_MACH; j = j + 1) begin: imem_mux
            assign imem_wr_mode[j] = en_sync[j];

            always_comb begin
                ram_wr_en[j] = 0;
                if (imem_wr_mode[j]) begin
                    // Core running — it owns the memory (REGISTER_RAM mode)
                    ram_wr_en[j]   = |mem_wstrb[j] & mem_valid[j];
                    ram_wr_mask[j] = mem_wstrb[j];
                    ram_wr_data[j] = mem_wdata[j];
                    ram_addr[j]    = mem_addr[j][MEM_ADDR_BITS+1:2];
                end else begin
                    // Core disabled — host can write/read via SPI
                    ram_wr_en[j]   = host_imem_wr && (host_imem_core == j[0:0]);
                    ram_wr_mask[j] = 4'b1111;
                    ram_wr_data[j] = host_imem_wdata;
                    ram_addr[j]    = host_imem_addr[MEM_ADDR_BITS-1:0];
                end
            end

            // Memory ready — REGISTER_RAM mode (from bio_bdma.sv lines 1834–1840)
            always_ff @(posedge clk) begin
                mem_valid_d[j] <= mem_valid[j] || (quanta_halt[j] && mem_valid_d[j]);
                dead_cycle[j] <= mem_valid_d[j] & mem_ready[j];
            end
            always_comb begin
                mem_ready[j] = mem_valid_d[j] && ~dead_cycle[j] && mem_valid[j];
            end
        end
    endgenerate

    // =====================================================================
    // STALL LOGIC (from bio_bdma.sv lines 686–710)
    // =====================================================================
    generate
        for (genvar j = 0; j < NUM_MACH; j = j + 1) begin: stalls
            always_comb begin
                // extclk MUST use 2-stage sync (bio_bdma.sv line 693)
                extclk_selected[j] = gpio_in_sync1[extclk_gpio[j]];
                stall[j] = (
                    quanta_halt[j] & ~quantum[j]
                    | (mach_regfifo_rd[j] &
                      ~{regfifo_readable[3], regfifo_readable[2],
                        regfifo_readable[1], regfifo_readable[0]}) != '0
                    | (mach_regfifo_wr[j] &
                      ~{regfifo_writable[3], regfifo_writable[2],
                        regfifo_writable[1], regfifo_writable[0]}) != '0
                    | stalling_for_event[j]
                ) || ~en_sync[j];
            end
            always_ff @(posedge clk ) begin

                quantum[j] <= use_extclk[j]
                    ? (extclk_selected[j] & ~extclk_selected_q[j])
                    : penable[j];
                extclk_selected_q[j] <= extclk_selected[j];

            end
        end
    endgenerate

    // =====================================================================
    // FIFO ARBITRATION (from bio_bdma.sv lines 712–778)
    // Pruned from 5 levels (4 cores + host) to 3 levels (2 cores + host).
    // Aliases removed (only used by BDMA).
    // =====================================================================
    generate
        for (genvar k = 0; k < 4; k = k + 1) begin: mach_to_fifo
            priority_demux #(.DATAW(32), .LEVELS(NUM_MACH + 1)) select_wdata (
                .stb({
                    mach_regfifo_wr[1][k] & en_sync[1],
                    mach_regfifo_wr[0][k] & en_sync[0],
                    push[k]
                }),
                .data_in({fdin[k], mach_regfifo_wdata[0], mach_regfifo_wdata[1]}),
                .data_out(regfifo_wdata[k])
            );
            priority_demux #(.DATAW(1), .LEVELS(NUM_MACH + 1)) select_wr (
                .stb({
                    mach_regfifo_wr[1][k] & en_sync[1],
                    mach_regfifo_wr[0][k] & en_sync[0],
                    push[k]
                }),
                .data_in({push[k], mach_regfifo_wr[0][k], mach_regfifo_wr[1][k]}),
                .data_out(regfifo_we[k])
            );
            priority_demux #(.DATAW(1), .LEVELS(NUM_MACH + 1)) select_rd (
                .stb({
                    mach_regfifo_rd[1][k] & en_sync[1],
                    mach_regfifo_rd[0][k] & en_sync[0],
                    pull[k]
                }),
                .data_in({pull[k], mach_regfifo_rd[0][k], mach_regfifo_rd[1][k]}),
                .data_out(regfifo_re[k])
            );
        end
    endgenerate

    // =====================================================================
    // EVENT LOGIC (from bio_bdma.sv lines 780–863)
    // Simplified: no aliases, no DMA events, no retiming pipeline.
    // =====================================================================
    logic [7:0] level_eq_result, level_lt_result, level_gt_result;
    logic [23:0] event_set_agg, event_clr_agg;

    generate
        for (genvar i = 0; i < 4; i = i + 1) begin: event_levels
            always_comb begin
                level_eq_result[2*i]   = (regfifo_level[i] == fifo_event_level[2*i]);
                level_eq_result[2*i+1] = (regfifo_level[i] == fifo_event_level[2*i+1]);
                level_lt_result[2*i]   = (regfifo_level[i] <  fifo_event_level[2*i]);
                level_lt_result[2*i+1] = (regfifo_level[i] <  fifo_event_level[2*i+1]);
                level_gt_result[2*i]   = (regfifo_level[i] >  fifo_event_level[2*i]);
                level_gt_result[2*i+1] = (regfifo_level[i] >  fifo_event_level[2*i+1]);
            end
        end
    endgenerate
    generate
        for (genvar i = 0; i < 8; i = i + 1) begin: event_levels_hookup
            always_ff @(posedge clk) begin
                aggregated_events[i + 24] <= fifo_event_eq_mask[i] && level_eq_result[i]
                    || fifo_event_lt_mask[i] && level_lt_result[i]
                    || fifo_event_gt_mask[i] && level_gt_result[i];
            end
        end
    endgenerate
    
    
    priority_demux #(.DATAW(24), .LEVELS(NUM_MACH + 1)) event_set_aggregator (
        .stb({event_set_valid[1] & en_sync[1],
              event_set_valid[0] & en_sync[0],
              host_event_set_valid}),
        .data_in({host_event_set, event_set[0], event_set[1]}),
        .data_out(event_set_agg)
    );
    priority_demux #(.DATAW(24), .LEVELS(NUM_MACH + 1)) event_clr_aggregator (
        .stb({event_clr_valid[1] & en_sync[1],
              event_clr_valid[0] & en_sync[0],
              host_event_clr_valid}),
        .data_in({host_event_clr, event_clr[0], event_clr[1]}),
        .data_out(event_clr_agg)
    );
    scc_ff #(.RESET(0), .WIDTH(24)) event_aggregator (
        .clk(clk), .reset_n(reset_n),
        .set(event_set_agg), .clr(event_clr_agg),
        .clobber('0), .value('0),
        .q(aggregated_events[23:0])
    );
    // =====================================================================
    // GPIO LOGIC (from bio_bdma.sv lines 865–1048)
    // 32-bit internal. sync_bypass uses gpio_in_sync0 (1-stage, not raw).
    // =====================================================================

    // Metastability hardening (bio_bdma.sv lines 879–886)
    always @(posedge clk) begin
        gpio_in_sync0 <= gpio_in;
        gpio_in_sync1 <= gpio_in_sync0;
    end
    generate
        for (genvar m = 0; m < 32; m = m + 1) begin: gen_bypass
            // 1 = 1-stage (faster), 0 = 2-stage (safer). Never raw.
            assign gpio_in_cleaned[m] = sync_bypass[m] ? gpio_in_sync0[m] : gpio_in_sync1[m];
        end
    endgenerate

    // GPIO arbitration — pruned to 2 cores
    // NOTE: strobes qualified with en_sync so a disabled core (unclocked,
    // undefined/random outputs) can never touch the shared GPIO state.
    logic [31:0] gpio_set_agg, gpio_clr_agg, gpdir_set_agg, gpdir_clr_agg;
    generate
        for (genvar g = 0; g < 32; g = g + 1) begin: gen_gpio
            priority_demux #(.DATAW(1), .LEVELS(NUM_MACH)) gpio_set_aggregator (
                .stb({gpio_set[1][g] & gpio_set_valid[1] & en_sync[1],
                      gpio_set[0][g] & gpio_set_valid[0] & en_sync[0]}),
                .data_in({gpio_set[0][g], gpio_set[1][g]}),
                .data_out(gpio_set_agg[g])
            );
            priority_demux #(.DATAW(1), .LEVELS(NUM_MACH)) gpio_clr_aggregator (
                .stb({gpio_clr[1][g] & gpio_clr_valid[1] & en_sync[1],
                      gpio_clr[0][g] & gpio_clr_valid[0] & en_sync[0]}),
                .data_in({gpio_clr[0][g], gpio_clr[1][g]}),
                .data_out(gpio_clr_agg[g])
            );
            priority_demux #(.DATAW(1), .LEVELS(NUM_MACH)) gpdir_set_aggregator (
                .stb({gpdir_set[1][g] & gpdir_set_valid[1] & en_sync[1],
                      gpdir_set[0][g] & gpdir_set_valid[0] & en_sync[0]}),
                .data_in({gpdir_set[0][g], gpdir_set[1][g]}),
                .data_out(gpdir_set_agg[g])
            );
            priority_demux #(.DATAW(1), .LEVELS(NUM_MACH)) gpdir_clr_aggregator (
                .stb({gpdir_clr[1][g] & gpdir_clr_valid[1] & en_sync[1],
                      gpdir_clr[0][g] & gpdir_clr_valid[0] & en_sync[0]}),
                .data_in({gpdir_clr[0][g], gpdir_clr[1][g]}),
                .data_out(gpdir_clr_agg[g])
            );
        end
    endgenerate

    scc_ff #(.RESET(0), .WIDTH(32)) gpio_aggregator (
        .clk(clk), .reset_n(reset_n),
        .set(gpio_set_agg), .clr(gpio_clr_agg),
        .clobber('0), .value('0), .q(gpio_out_int)
    );
    scc_ff #(.RESET(0), .WIDTH(32)) gpdir_aggregator (
        .clk(clk), .reset_n(reset_n),
        .set(gpdir_set_agg), .clr(gpdir_clr_agg),
        .clobber('0), .value('0), .q(gpio_dir_int)
    );

    // Snap-to-quantum output (2 cores, from bio_bdma.sv lines 965–1008)

    always_ff @(posedge clk or negedge reset_n) begin
        if (~reset_n) begin
            gpio_out <= 32'h0;
            gpio_dir <= 32'h0;      // all inputs (hi-Z pads) out of reset
        end else if (snap_output_to_quantum) begin
            case (snap_output_to_which)
                1'b0: if (penable[0]) begin gpio_out <= gpio_out_int; gpio_dir <= gpio_dir_int; end
                1'b1: if (penable[1]) begin gpio_out <= gpio_out_int; gpio_dir <= gpio_dir_int; end
            endcase
        end else begin
            gpio_out <= gpio_out_int;
            gpio_dir <= gpio_dir_int;
        end
    end

    // Snap-to-quantum input (2 cores, from bio_bdma.sv lines 1010–1048)
    always_ff @(posedge clk) begin
        case (snap_input_to_which)
            1'b0: if (penable[0]) gpio_in_snapped <= gpio_in_cleaned;
            1'b1: if (penable[1]) gpio_in_snapped <= gpio_in_cleaned;
        endcase
    end
    always_comb begin
        gpio_in_maybe_snapped = snap_input_to_quantum ? gpio_in_snapped : gpio_in_cleaned;
    end

    // =====================================================================
    // IRQ LOGIC (from bio_bdma.sv lines 1050–1074, reduced to 2 channels)
    // =====================================================================
    generate
        for (genvar i = 0; i < NUM_MACH; i = i + 1) begin: irq_gen
            always_comb begin
                irq_agg[i] = (irqmask[i] & aggregated_events) != 0;
            end
        end
    endgenerate
    always_ff @(posedge clk) begin irq_agg_q <= irq_agg; end
    generate
        for (genvar i = 0; i < NUM_MACH; i = i + 1) begin: IRQs
            always_ff @(posedge clk or negedge reset_n) begin
                if (~reset_n) irq[i] <= '0;
                else irq[i] <= irq_edge[i] ? (irq_agg[i] & ~irq_agg_q[i]) : irq_agg[i];
            end
        end
    endgenerate


    logic [NUM_MACH-1:0]       a_restart;
    logic [NUM_MACH-1:0]       a_restart_q[2];

    always_ff @ (posedge clk) begin 
        
        a_restart_q[0] <= restart;
        a_restart_q[1] <= a_restart_q[0];
        a_restart <= a_restart_q[1];

   end

    // =====================================================================
    // CORE INSTANTIATION (from bio_bdma.sv lines 1703–1963)
    // PROGADDR_RESET = 0, ENABLE_FAST_MUL = 1 (taped-out config)
    // =====================================================================
    generate
        for (genvar j = 0; j < NUM_MACH; j = j + 1) begin: mach
            // Enable sync — aligns with ctl_action
            always_ff @(posedge clk) begin
                if (~reset_n) en_sync[j] <= 0;
                else if (ctl_action) en_sync[j] <= en[j];
            end

            // Per-core clock counter
            always_ff @(posedge clk) begin
                if (en_sync[j] == 0) core_clk_count[j] <= '0;
                else core_clk_count[j] <= core_clk_count[j] + 30'h1;
            end

            // Quantum clock divider
            pio_divider clk_divider (
                .clk(clk), .reset(reset | clkdiv_restart[j]),
                .div_int(div_int[j]), .div_frac(div_frac[j]),
                .penable(penable[j])
            );

            // Clock gate
            //gf180mcu_fd_sc_mcu7t5v0__icgtp_1 icg (.CLK(clk), .E(~stall[j]), .TE(1'b0), .Q(core_clk[j]));


            `ifdef USE_GF180

            gf180mcu_fd_sc_mcu7t5v0__icgtp_1 icg (.CLK(clk), .E(~stall[j]|a_restart[j]), .TE(1'b0), .Q(core_clk[j]));
            
            `else
            
            reg icg_latch;
            always @(*) begin
                if (!clk) icg_latch = ~stall[j];
            end
            assign core_clk[j] = clk & icg_latch;
            `endif

            // PicoRV32 core
            picorv32 #(
                .ENABLE_COUNTERS(0), .ENABLE_COUNTERS64(0),
                .ENABLE_REGS_16_31(1), .ENABLE_REGS_DUALPORT(1),
                .LATCHED_MEM_RDATA(0),
                .TWO_STAGE_SHIFT(1), .BARREL_SHIFTER(1),
                .TWO_CYCLE_COMPARE(0), .TWO_CYCLE_ALU(0),
                .COMPRESSED_ISA(1),
                .CATCH_MISALIGN(0), .CATCH_ILLINSN(1),
                .ENABLE_PCPI(0),
                .ENABLE_MUL(0), .ENABLE_FAST_MUL(1),
                .ENABLE_DIV(0),
                .ENABLE_IRQ(0), .ENABLE_IRQ_QREGS(0), .ENABLE_IRQ_TIMER(0),
                .ENABLE_TRACE(0), .REGS_INIT_ZERO(0),
                .MASKED_IRQ(32'h0000_0000), .LATCHED_IRQ(32'hffff_ffff),
                .PROGADDR_RESET(0),
                .PROGADDR_IRQ(32'h0000_0010),
                .STACKADDR(MEM_SIZE_BYTES - 1),
                .NUM_MACH(NUM_MACH)
            ) core (
                .clk(core_clk[j]), .resetn(reset_n & ~restart[j]), .trap(trap[j]),
                // Memory — direct to per-core IMEM (no DMA merge)
                .mem_valid(mem_valid[j]), .mem_instr(mem_instr[j]),
                .mem_ready(mem_ready[j]), .mem_rdata(mem_rdata[j]),
                .mem_addr(mem_addr[j]), .mem_wdata(mem_wdata[j]), .mem_wstrb(mem_wstrb[j]),
                .mem_la_read(mem_la_read[j]), .mem_la_write(mem_la_write[j]),
                .mem_la_addr(mem_la_addr[j]), .mem_la_wdata(mem_la_wdata[j]),
                .mem_la_wstrb(mem_la_wstrb[j]),
                // BIO register file
                .regfifo_rdata_0(regfifo_rdata[0]), .regfifo_rdata_1(regfifo_rdata[1]),
                .regfifo_rdata_2(regfifo_rdata[2]), .regfifo_rdata_3(regfifo_rdata[3]),
                .regfifo_rd(mach_regfifo_rd[j]), .regfifo_wdata(mach_regfifo_wdata[j]),
                .regfifo_wr(mach_regfifo_wr[j]),
                .quanta_halt(quanta_halt[j]),
                .gpio_set(gpio_set[j]), .gpio_clr(gpio_clr[j]),
                .gpdir_set(gpdir_set[j]), .gpdir_clr(gpdir_clr[j]),
                .gpio_set_valid(gpio_set_valid[j]), .gpio_clr_valid(gpio_clr_valid[j]),
                .gpdir_set_valid(gpdir_set_valid[j]), .gpdir_clr_valid(gpdir_clr_valid[j]),
                .gpio_pins(gpio_in_maybe_snapped),
                .aggregated_events(aggregated_events),
                .stalling_for_event(stalling_for_event[j]),
                .event_set(event_set[j]), .event_set_valid(event_set_valid[j]),
                .event_clr(event_clr[j]), .event_clr_valid(event_clr_valid[j]),
                .core_id(j[0]), .clk_count(core_clk_count[j]),
                // Unused
                .pcpi_valid(), .pcpi_insn(), .pcpi_rs1(), .pcpi_rs2(),
                .pcpi_wr('0), .pcpi_rd('0), .pcpi_wait('0), .pcpi_ready('0),
                .irq('0), .eoi(), .trace_valid(), .trace_data()
            );
        end
    endgenerate

// =====================================================================
    // FIFO INSTANTIATION (always 4 FIFOs)
    // bio_bdma semantics (line 1969): FIFO k is reset by restart of core k.
    // With NUM_MACH=2, FIFOs 2..3 have no owning core: cleared only by
    // host FIFO_CLR (reg 0x48) or chip reset.
    // =====================================================================
    generate
        for (genvar k = 0; k < 4; k = k + 1) begin: fifos
            wire fifo_restart;
            if (k < NUM_MACH) begin : g_core_rst
                assign fifo_restart = restart[k];
            end else begin : g_no_core_rst
                assign fifo_restart = 1'b0;
            end

            regfifo regfifo (
                .reset(reset | (fifo_to_reset[k] & do_fifo_clr) | fifo_restart),
                .aclk(clk),
                .wdata(regfifo_wdata[k]), .we(regfifo_we[k]), .writable(regfifo_writable[k]),
                .re(regfifo_re[k]), .readable(regfifo_readable[k]),
                .rdata(regfifo_rdata[k]), .level(regfifo_level[k])
            );
        end
    endgenerate



endmodule



// =========================================================================
// HELPER MODULES (from bio_bdma.sv, unchanged)
// =========================================================================

module priority_demux #(
    parameter DATAW = 32,
    parameter LEVELS = 4
) (
    input [LEVELS-1:0]       stb,
    input [DATAW-1:0]        data_in [LEVELS],
    output logic [DATAW-1:0] data_out
);
    always_comb begin
        data_out = '0;
        for (int i = LEVELS - 1; i >= 0; i = i - 1) begin: priorities
            if (stb[i]) data_out = data_in[i];
        end
    end
endmodule

module scc_ff #(
    parameter RESET = '0,
    parameter WIDTH = 1
) (
    input clk, input reset_n,
    input [WIDTH-1:0] set, input [WIDTH-1:0] clr,
    input [WIDTH-1:0] clobber, input [WIDTH-1:0] value,
    output logic [WIDTH-1:0] q
);
    generate
        for (genvar i = 0; i < WIDTH; i = i + 1) begin: scc_gen
            always_ff @(posedge clk or negedge reset_n) begin
                if (!reset_n) q[i] <= RESET[i];
                else if (clobber[i]) q[i] <= value[i];
                else if (set[i] && !clr[i]) q[i] <= 1'b1;
                else if (!set[i] && clr[i]) q[i] <= 1'b0;
            end
        end
    endgenerate
endmodule

// picorv32_regs_bio — the core BIO "magic" (from bio_bdma.sv lines 2034–2232)
// Event stalling uses == 0 (bio_bdma.sv), NOT != event_mask (abandoned bio.sv)
module picorv32_regs_bio #(
    parameter NUM_MACH = 4,
    parameter NUM_MACH_BITS = $clog2(NUM_MACH)
)(
    input [31:0] regfifo_rdata_0, input [31:0] regfifo_rdata_1,
    input [31:0] regfifo_rdata_2, input [31:0] regfifo_rdata_3,
    output logic [3:0]  regfifo_rd,
    output logic [31:0] regfifo_wdata,
    output logic [3:0]  regfifo_wr,
    output logic quanta_halt,
    output logic [31:0] gpio_set, output logic [31:0] gpio_clr,
    output logic [31:0] gpdir_set, output logic [31:0] gpdir_clr,
    output logic gpio_set_valid, output logic gpio_clr_valid,
    output logic gpdir_set_valid, output logic gpdir_clr_valid,
    input [31:0] gpio_pins,
    input [31:0] aggregated_events,
    output logic stalling_for_event,
    output logic [23:0] event_set, output logic event_set_valid,
    output logic [23:0] event_clr, output logic event_clr_valid,
    input [NUM_MACH_BITS-1:0] core_id,
    input [31 - NUM_MACH_BITS:0] clk_count,
    input clk, input reset_n,
    input wen, input ren1, input ren2,
    input [5:0] waddr, input [5:0] raddr1, input [5:0] raddr2,
    input [31:0] wdata,
    output logic [31:0] rdata1, output logic [31:0] rdata2
);
    logic [31:0] regs [0:14];
    logic [31:0] gpio_mask, event_mask;
    logic quanta_wr, quanta_rd;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) gpio_mask <= 32'hffff_ffff;
        else if (wen & (waddr == 6'd26)) gpio_mask <= wdata;
    end
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) event_mask <= 32'h0;
        else if (wen & (waddr == 6'd27)) event_mask <= wdata;
    end
    always_ff @(posedge clk) begin
        if (wen && (waddr[5:4] == 2'b00)) regs[~waddr[3:0]] <= wdata;
    end

    assign quanta_halt = quanta_wr || quanta_rd;

    // Write path
    always_comb begin
        regfifo_wr = '0; quanta_wr = 0;
        gpio_set_valid = 0; gpio_clr_valid = 0;
        gpdir_set = '0; gpdir_set_valid = 0; gpdir_clr_valid = 0;
        event_set_valid = 0; event_clr_valid = 0;
        regfifo_wdata = wdata;
        gpio_set = gpio_mask & wdata;
        gpio_clr = gpio_mask & ~wdata;
        gpdir_set = gpio_mask & wdata;
        gpdir_clr = gpio_mask & wdata;
        event_set = wdata[23:0]; event_clr = wdata[23:0];
        // bio_bdma.sv semantics: stall when NO events match (== 0)
        stalling_for_event = ((event_mask & aggregated_events) == 0) && (event_mask != 0) &&
            ((ren1 && (raddr1 == 30)) || (ren2 && (raddr2 == 30)));
        if (wen) begin
            casez (waddr)
                6'b0100??: regfifo_wr[waddr[1:0]] = 1'b1;
                6'b010100: quanta_wr = 1;
                6'b010101: begin gpio_set_valid = 1; gpio_clr_valid = 1; end
                6'b010110: gpio_set_valid = 1;
                6'b010111: gpio_clr_valid = 1;
                6'b011000: gpdir_set_valid = 1;
                6'b011001: gpdir_clr_valid = 1;
                6'b011100: event_set_valid = 1;
                6'b011101: event_clr_valid = 1;
            endcase
        end
    end

    // Read path
    always_comb begin
        regfifo_rd = 4'h0; rdata1 = 32'h0; rdata2 = 32'h0; quanta_rd = 0;
        casez (raddr1)
            6'b00????: rdata1 = regs[~raddr1[3:0]];
            6'b010000: begin rdata1 = regfifo_rdata_0; regfifo_rd[0] = ren1; end
            6'b010001: begin rdata1 = regfifo_rdata_1; regfifo_rd[1] = ren1; end
            6'b010010: begin rdata1 = regfifo_rdata_2; regfifo_rd[2] = ren1; end
            6'b010011: begin rdata1 = regfifo_rdata_3; regfifo_rd[3] = ren1; end
            6'b010100: begin rdata1 = 0; quanta_rd = ren1; end
            6'b010101: rdata1 = gpio_pins;
            6'b011010: rdata1 = gpio_mask;
            6'b011011: rdata1 = event_mask;
            6'b011110: rdata1 = aggregated_events;
            6'b011111: rdata1 = {core_id, clk_count};
        endcase
        casez (raddr2)
            6'b00????: rdata2 = regs[~raddr2[3:0]];
            6'b010000: begin rdata2 = regfifo_rdata_0; regfifo_rd[0] = ren2; end
            6'b010001: begin rdata2 = regfifo_rdata_1; regfifo_rd[1] = ren2; end
            6'b010010: begin rdata2 = regfifo_rdata_2; regfifo_rd[2] = ren2; end
            6'b010011: begin rdata2 = regfifo_rdata_3; regfifo_rd[3] = ren2; end
            6'b010100: begin rdata2 = 0; quanta_rd = ren2; end
            6'b010101: rdata2 = gpio_pins;
            6'b011010: rdata2 = gpio_mask;
            6'b011011: rdata2 = event_mask;
            6'b011110: rdata2 = aggregated_events;
            6'b011111: rdata2 = {core_id, clk_count};
        endcase
    end
endmodule
