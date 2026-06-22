module ICG (
    input  wire CK,
    input  wire EN,
    input  wire SE,
    output wire CKG
);
    reg en_r;
    always @(negedge CK) begin
        en_r <= EN | SE;
    end
    assign CKG = CK & en_r;
endmodule