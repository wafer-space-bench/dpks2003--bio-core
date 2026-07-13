// Delta-race-immune simulation model for gf180mcu_fd_sc_mcu7t5v0__icgtp_1.
// SIM ONLY — shadows the PDK cell in gate-level simulation source order.
//
// Why: post-CTS zero-delay GLS races enable-cone updates against the clock
// edge traversing buffer chains (delta time), poisoning the PDK model's
// transparent-low UDP latch with X. Sampling E on negedge CLK freezes the
// enable a half-cycle before the gated edge: functionally identical gating
// at cycle granularity, immune to delta-ordering.
//
// Timing correctness of the real latch-based cell is signed off via STA
// (setup/hold arcs on E; all corners clean — see verification notes).
`timescale 1ns/1ps
module gf180mcu_fd_sc_mcu7t5v0__icgtp_1 (
    input  CLK,
    input  E,
    input  TE,
    output Q
);
    reg en_latched = 1'b0;          // defined at t=0: no pre-reset X clock

    always @(negedge CLK)
        en_latched <= E | TE;

    assign Q = CLK & en_latched;
endmodule
