`timescale 1ns/1ps
// Ten-clock streaming path. A three-pixel horizontal filter uses no frame/line RAM.
// RGB shares a gain indexed by max(R,G,B), preventing independent-channel clipping.
module hdmi_lowlight_v2 #(
    parameter SHOW_OSD = 1
)(
    input wire clk, input wire rst_n,
    input wire [1:0] i_strength,
    input wire i_vs, input wire i_hs, input wire i_de,
    input wire [7:0] i_r, input wire [7:0] i_g, input wire [7:0] i_b,
    output reg o_vs, output reg o_hs, output reg o_de,
    output reg [7:0] o_r, output reg [7:0] o_g, output reg [7:0] o_b
);
    reg [1:0] strength_meta, strength_sync, strength_frame;
    reg input_vs_d;
    // Select strength only at a frame boundary, avoiding a mid-frame brightness seam.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            strength_meta<=1; strength_sync<=1; strength_frame<=1; input_vs_d<=0;
        end else begin
            strength_meta<=i_strength; strength_sync<=strength_meta; input_vs_d<=i_vs;
            if(i_vs && !input_vs_d) strength_frame<=strength_sync>2 ? 2'd2 : strength_sync;
        end
    end
    reg [7:0] r_prev, g_prev, b_prev, r_prev2, g_prev2, b_prev2;
    reg prev_vs, prev_hs, prev_de, prev2_de;
    reg [7:0] r_s1,g_s1,b_s1,r_s2,g_s2,b_s2,r_s3,g_s3,b_s3;
    reg [7:0] maximum_s2, r_s4,g_s4,b_s4,r_s5,g_s5,b_s5;
    reg [9:0] gain_s4;
    wire [29:0] gains_s3;
    reg [17:0] r_product_s4,g_product_s4,b_product_s4;
    reg [1:0] strength_s1,strength_s2,strength_s3,strength_s4,strength_product,strength_s5;
    reg [5:0] vs_pipe,hs_pipe,de_pipe;
    wire [9:0] gain_s3 = strength_s3==0 ? gains_s3[9:0] :
                          strength_s3==1 ? gains_s3[19:10] : gains_s3[29:20];
    function near_value;
        input [7:0] a,b;
        begin near_value = a>b ? (a-b<=12) : (b-a<=12); end
    endfunction
    function [7:0] weighted;
        input [7:0] a,b,c;
        reg [9:0] sum;
        begin sum={2'b0,a}+({2'b0,b}<<1)+{2'b0,c}+10'd2; weighted=sum[9:2]; end
    endfunction
    function [7:0] max_rgb;
        input [7:0] r,g,b;
        begin max_rgb=r>g ? (r>b?r:b) : (g>b?g:b); end
    endfunction
    wire filter_ok = prev_de && prev2_de && i_de && max_rgb(r_prev,g_prev,b_prev)<112 &&
        near_value(i_r,r_prev) && near_value(r_prev2,r_prev) &&
        near_value(i_g,g_prev) && near_value(g_prev2,g_prev) &&
        near_value(i_b,b_prev) && near_value(b_prev2,b_prev);

    hdmi_lowlight_gain_rom u_gain_rom(.clk(clk),.address(maximum_s2),.gains(gains_s3));

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            r_prev<=0;g_prev<=0;b_prev<=0;r_prev2<=0;g_prev2<=0;b_prev2<=0;
            prev_vs<=0;prev_hs<=0;prev_de<=0;prev2_de<=0;
            r_s1<=0;g_s1<=0;b_s1<=0;r_s2<=0;g_s2<=0;b_s2<=0;r_s3<=0;g_s3<=0;b_s3<=0;
            r_s4<=0;g_s4<=0;b_s4<=0;gain_s4<=256;
            maximum_s2<=0;r_product_s4<=0;g_product_s4<=0;b_product_s4<=0;
            r_s5<=0;g_s5<=0;b_s5<=0;vs_pipe<=0;hs_pipe<=0;de_pipe<=0;
            strength_s1<=1;strength_s2<=1;strength_s3<=1;strength_s4<=1;strength_product<=1;strength_s5<=1;
        end else begin
            r_prev<=i_r;g_prev<=i_g;b_prev<=i_b;r_prev2<=r_prev;g_prev2<=g_prev;b_prev2<=b_prev;
            prev_vs<=i_vs;prev_hs<=i_hs;prev_de<=i_de;prev2_de<=prev_de;
            r_s1<=filter_ok?weighted(r_prev2,r_prev,i_r):r_prev;
            g_s1<=filter_ok?weighted(g_prev2,g_prev,i_g):g_prev;
            b_s1<=filter_ok?weighted(b_prev2,b_prev,i_b):b_prev;
            r_s2<=r_s1;g_s2<=g_s1;b_s2<=b_s1;maximum_s2<=max_rgb(r_s1,g_s1,b_s1);
            r_s3<=r_s2;g_s3<=g_s2;b_s3<=b_s2;
            // Separate block-ROM clock-to-out and mux delay from DSP setup time.
            gain_s4<=gain_s3;r_s4<=r_s3;g_s4<=g_s3;b_s4<=b_s3;
            r_product_s4<=r_s4*gain_s4+18'd128;
            g_product_s4<=g_s4*gain_s4+18'd128;
            b_product_s4<=b_s4*gain_s4+18'd128;
            r_s5<=r_product_s4[15:8];g_s5<=g_product_s4[15:8];b_s5<=b_product_s4[15:8];
            vs_pipe<={vs_pipe[4:0],prev_vs};hs_pipe<={hs_pipe[4:0],prev_hs};de_pipe<={de_pipe[4:0],prev_de};
            strength_s1<=strength_frame;strength_s2<=strength_s1;strength_s3<=strength_s2;
            strength_s4<=strength_s3;strength_product<=strength_s4;strength_s5<=strength_product;
        end
    end

    reg [11:0] x,y;
    reg vs_last,de_last;
    wire badge=SHOW_OSD && x>=16 && x<142 && y>=16 && y<44;
    wire bars = y>=24 && y<36 && ((x>=100 && x<108) ||
        (strength_s5>=1 && x>=112 && x<120) || (strength_s5>=2 && x>=124 && x<132));
    // LOW 1/2/3, fixed 3x5 rows at 4x scale. Two OSD pipeline stages
    // separate row decode, horizontal selection and the final RGB mux.
    reg [2:0] digit_row;
    reg [15:0] row_pattern;
    always @* begin
        digit_row=0;row_pattern=0;
        case(y[5:2])
            5:begin
                digit_row=strength_s5==0?3'b010:3'b110;
                row_pattern={3'b100,1'b0,3'b111,1'b0,3'b101,1'b0,digit_row,1'b0};
            end
            6:begin
                digit_row=strength_s5==0?3'b110:3'b001;
                row_pattern={3'b100,1'b0,3'b101,1'b0,3'b101,1'b0,digit_row,1'b0};
            end
            7:begin
                row_pattern={3'b100,1'b0,3'b101,1'b0,3'b101,1'b0,3'b010,1'b0};
            end
            8:begin
                digit_row=strength_s5==0?3'b010:strength_s5==1?3'b100:3'b001;
                row_pattern={3'b100,1'b0,3'b101,1'b0,3'b111,1'b0,digit_row,1'b0};
            end
            9:begin
                digit_row=strength_s5==2?3'b110:3'b111;
                row_pattern={3'b111,1'b0,3'b111,1'b0,3'b101,1'b0,digit_row,1'b0};
            end
            default:begin digit_row=0;row_pattern=0;end
        endcase
    end
    reg badge_s6,bars_s6,text_area_s6,vs_s6,hs_s6,de_s6;
    reg [3:0] column_s6;
    reg [15:0] pattern_s6;
    reg [7:0] r_s6,g_s6,b_s6,r_s7,g_s7,b_s7;
    reg badge_s7,foreground_s7,vs_s7,hs_s7,de_s7;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)begin
            badge_s6<=0;bars_s6<=0;text_area_s6<=0;vs_s6<=0;hs_s6<=0;de_s6<=0;
            column_s6<=0;pattern_s6<=0;r_s6<=0;g_s6<=0;b_s6<=0;
            badge_s7<=0;foreground_s7<=0;vs_s7<=0;hs_s7<=0;de_s7<=0;
            r_s7<=0;g_s7<=0;b_s7<=0;
        end else begin
            badge_s6<=badge;bars_s6<=bars;
            text_area_s6<=x>=32 && x<96 && y>=20 && y<40;
            column_s6<=~(x[5:2]^4'd8);pattern_s6<=row_pattern;
            vs_s6<=vs_pipe[5];hs_s6<=hs_pipe[5];de_s6<=de_pipe[5];
            r_s6<=r_s5;g_s6<=g_s5;b_s6<=b_s5;
            badge_s7<=badge_s6;foreground_s7<=bars_s6 || (text_area_s6 && pattern_s6[column_s6]);
            vs_s7<=vs_s6;hs_s7<=hs_s6;de_s7<=de_s6;
            r_s7<=r_s6;g_s7<=g_s6;b_s7<=b_s6;
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            x<=0;y<=0;vs_last<=0;de_last<=0;
            o_vs<=0;o_hs<=0;o_de<=0;o_r<=0;o_g<=0;o_b<=0;
        end else begin
            vs_last<=vs_pipe[5];de_last<=de_pipe[5];
            if(vs_pipe[5] && !vs_last) begin x<=0;y<=0;end
            else if(de_pipe[5]) x<=x+1'b1;
            else if(de_last) begin x<=0;y<=y+1'b1;end
            o_vs<=vs_s7;o_hs<=hs_s7;o_de<=de_s7;
            if(!de_s7) begin o_r<=0;o_g<=0;o_b<=0;end
            else if(badge_s7 && foreground_s7) begin o_r<=72;o_g<=224;o_b<=232;end
            else if(badge_s7) begin o_r<=8;o_g<=20;o_b<=28;end
            else begin o_r<=r_s7;o_g<=g_s7;o_b<=b_s7;end
        end
    end
endmodule
