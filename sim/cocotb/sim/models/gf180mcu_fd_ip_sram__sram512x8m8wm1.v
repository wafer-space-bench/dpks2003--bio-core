/*
 * $Id: $
 * Copyright 2022 GlobalFoundries PDK Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http:www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * Project:             018 5VGREEN SRAM
 * Author:              GlobalFoundries PDK Authors
 * Data Created:        05-06-2014
 * Revision:		0.0
 *
 * Description:         gf180mcu_fd_ip_sram__sram512x8m8wm1 Simulation Model
 */

`timescale 1 ps / 1 ps

module gf180mcu_fd_ip_sram__sram512x8m8wm1 (
	CLK,
	CEN,
	GWEN,
	WEN,
	A,
	D,
	Q,
	VDD,
	VSS
);

input           CLK;
input           CEN;    //Chip Enable Negative
input           GWEN;   //Global Write Enable Negative
input   [7:0]  	WEN;    //Write Enable Negative
input   [8:0]   A;
input   [7:0]  	D;
output	[7:0]	Q;
inout		VDD;
inout		VSS;


`ifdef VERILATOR
localparam Tdly = 100;  // patch: specify-block specparam is ignored
`endif

reg	[7:0]	mem[511:0];
reg	[7:0]	qo_reg;

wire		cen_flag;
wire		write_flag;
wire		read_flag;

reg             ntf_Tcyc;	//notifier for clock period/low/high pulse
reg             ntf_Tckh;
reg             ntf_Tckl;

reg		ntf_tcs;	//notifier for setup time
reg		ntf_tas;
reg		ntf_tds;
reg		ntf_tws;
reg		ntf_twis;

reg             ntf_tch;	//notifier for hold time
reg             ntf_tah;
reg             ntf_tdh;
reg             ntf_twh;
reg             ntf_twih;

wire		no_st_viol;	//no setup violation
wire		no_hd_viol;	//no hold violation
wire		no_ck_viol;	//no clock related violation

reg             clk_dly;        //for read/write
reg             write_flag_dly; //for write invalidation
reg             read_flag_dly;  //for read invalidation
reg             cen_dly;
reg             cen_fell;       //detect CEN 1 -> 0 transition
reg             cen_not_rst;    //detect CEN is not reset initially

wire    [7:0]  we;       	//inversion of WEN
wire    [7:0]  cd2;
wire    [7:0]  cd4;
wire    [7:0]  cd5;
reg    	[7:0]  cdx;

reg	[8:0]	marked_a;

integer         i;

assign Q = qo_reg;

//---- for debugging
wire    [7:0]  mem_0;
wire	[7:0]  mem_1;
wire	[7:0]  mem_2;
wire	[7:0]  mem_3;
assign mem_0 = mem[0];
assign mem_1 = mem[1];
assign mem_2 = mem[2];
assign mem_3 = mem[3];

always @(CEN) cen_dly = #100 CEN;
always @(CEN or cen_dly) begin
  if (!CEN & cen_dly) cen_fell = 1'b1;
end

always @(posedge CLK) begin
  if (!CEN & !cen_fell & !cen_not_rst) cen_not_rst = 1;
end

always @(posedge cen_not_rst) begin
  $display("-------- WARNING: CEN is not reset, memory is not operational ---------");
  $display("-------- @Time %0t: scope = %m", $realtime, " ---------");
end

always @(posedge cen_fell) begin
  $display("-------- MESSAGE: CEN is just reset, memory is operational ---------");
  $display("-------- @Time %0t: scope = %m", $realtime, " ---------");
end

assign cen_flag   =  cen_fell & !CEN;
assign write_flag =  cen_fell & !CEN & !GWEN & !(&WEN);
assign read_flag  =  cen_fell & !CEN &  GWEN;

reg cen_flag_dly;
always @(cen_flag) cen_flag_dly = #100 cen_flag;


assign no_st_viol = ~(|{ntf_tcs, ntf_tas, ntf_tds, ntf_tws, ntf_twis});
assign no_hd_viol = ~(|{ntf_tch, ntf_tah, ntf_tdh, ntf_twh, ntf_twih});
assign no_ck_viol = ~(|{ntf_Tcyc, ntf_Tckh, ntf_Tckl});

always @(CLK) clk_dly        = #Tdly CLK;
always @(CLK) write_flag_dly = #200 write_flag;
always @(CLK) read_flag_dly  = #200 read_flag;

always @(posedge CLK) marked_a = A;

assign we  = ~WEN;
assign cd2 = mem[A] & WEN;	//set write bits to 0, others unchanged
assign cd4 = D & we;		//set write bits to 0/1, others = 0
assign cd5 = cd2 | cd4;		//memory content after write

always @(posedge CLK) cdx = {8{1'bx}} & we;    //latch cdx

always @(posedge clk_dly) begin
  if (write_flag) begin 	//write
    if (no_st_viol) begin 	//write, no viol
      mem[A] = cd5;
    end
    else begin                 	//write, with viol
      mem[A] = mem[A] ^ cdx;    //1^x = x
      qo_reg = qo_reg ^ cdx;
    end
  end //write
  else if (read_flag) begin     //read
    if (no_st_viol) begin 	//read, no viol
      qo_reg = mem[marked_a];
    end
    else begin                  //read, with viol
      qo_reg = 8'bx;
    end
  end //read
end

always @(negedge clk_dly) begin         	//invalidate write/read when hold/clk viol
  if (no_hd_viol == 0 | no_ck_viol == 0) begin
    if (write_flag_dly) begin
      if (ntf_twh) begin
        mem[marked_a] = mem[marked_a] ^ 8'bx; //GWEN can't be used to generate cdx
        qo_reg        = qo_reg ^ 8'bx;
      end
      else begin
        mem[marked_a] = mem[marked_a] ^ cdx;
        qo_reg        = qo_reg ^ cdx;
      end
    end
    else if (read_flag_dly) begin
      qo_reg = 8'bx;
    end

    #100;
    ntf_tch  = 0;
    ntf_tah  = 0;
    ntf_tdh  = 0;
    ntf_twh  = 0;
    ntf_twih = 0;

    ntf_Tcyc  = 0;
    ntf_Tckh  = 0;
    ntf_Tckl  = 0;
  end
  else begin
    #100;
    ntf_tch  = 0;
    ntf_tah  = 0;
    ntf_tdh  = 0;
    ntf_twh  = 0;
    ntf_twih = 0;

    ntf_Tcyc  = 0;
    ntf_Tckh  = 0;
    ntf_Tckl  = 0;
  end
end

always @(posedge ntf_tcs or posedge ntf_tas or posedge ntf_tds or
         posedge ntf_tws or posedge ntf_twis or
         posedge ntf_tch or posedge ntf_tah or posedge ntf_tdh or
         posedge ntf_twh or posedge ntf_twih or
         posedge ntf_Tcyc or posedge ntf_Tckh or posedge ntf_Tckl) begin
  if (cen_fell) begin
    #Tdly;
    if (ntf_tcs)  $display("---- ERROR: CEN setup violation! ----");
    if (ntf_tas)  $display("---- ERROR: A setup violation! ----");
    if (ntf_tds)  $display("---- ERROR: D setup violation! ----");
    if (ntf_tws)  $display("---- ERROR: GWEN setup violation! ----");
    if (ntf_twis) $display("---- ERROR: WEN setup violation! ----");

    if (ntf_tch)  $display("---- ERROR: CEN hold violation! ----");
    if (ntf_tah)  $display("---- ERROR: A hold violation! ----");
    if (ntf_tdh)  $display("---- ERROR: D hold violation! ----");
    if (ntf_twh)  $display("---- ERROR: GWEN hold violation! ----");
    if (ntf_twih) $display("---- ERROR: WEN hold violation! ----");

    if (ntf_Tcyc) $display("---- ERROR: CLK period violation! ----");
    if (ntf_Tckh) $display("---- ERROR: CLK pulse width high violation! ----");
    if (ntf_Tckl) $display("---- ERROR: CLK pulse width low violation! ----");
  end
end

always @(posedge cen_fell) begin	//reset fasle notifiers
  ntf_tcs  = 0;				//after CEN reset (CEN from 1 to 0)
  ntf_tas  = 0;
  ntf_tds  = 0;
  ntf_tws  = 0;
  ntf_twis = 0;

  ntf_tch  = 0;
  ntf_tah  = 0;
  ntf_tdh  = 0;
  ntf_twh  = 0;
  ntf_twih = 0;
end

always @(negedge clk_dly) begin	//reset setup/hold notifiers
  #100;
  ntf_tcs  = 0;
  ntf_tas  = 0;
  ntf_tds  = 0;
  ntf_tws  = 0;
  ntf_twis = 0;

  ntf_tch  = 0;
  ntf_tah  = 0;
  ntf_tdh  = 0;
  ntf_twh  = 0;
  ntf_twih = 0;
end

initial begin			//initialization
  ntf_Tcyc  = 0;
  ntf_Tckh  = 0;
  ntf_Tckl  = 0;

  ntf_tcs  = 0;
  ntf_tas  = 0;
  ntf_tds  = 0;
  ntf_tws  = 0;
  ntf_twis = 0;

  ntf_tch  = 0;
  ntf_tah  = 0;
  ntf_tdh  = 0;
  ntf_twh  = 0;
  ntf_twih = 0;

  marked_a = 9'd0;

  qo_reg         = 8'd0;
  clk_dly        = 0;
  write_flag_dly = 0;
  read_flag_dly  = 0;
  cen_dly        = 0;
  cen_fell       = 0;
  cen_not_rst    = 0;

  for(i=0; i<512; i=i+1) begin
    mem[i] = 8'd0;
  end
end

endmodule
