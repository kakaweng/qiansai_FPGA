`timescale 1ns / 1ps

module video_async_fifo #(
    parameter DATA_WIDTH = 259,
    parameter ADDR_BITS = 7,
    parameter RAM_STYLE = "auto"
)(
    input wire rst_n,
    input wire wr_clk,
    input wire wr_valid,
    output wire wr_ready,
    input wire [DATA_WIDTH-1:0] wr_data,
    input wire rd_clk,
    output reg rd_valid,
    input wire rd_ready,
    output reg [DATA_WIDTH-1:0] rd_data
);
    (* syn_ramstyle = RAM_STYLE *) reg [DATA_WIDTH-1:0] memory [0:(1 << ADDR_BITS)-1];
    reg [ADDR_BITS:0] wr_binary;
    reg [ADDR_BITS:0] wr_gray;
    reg [ADDR_BITS:0] rd_binary;
    reg [ADDR_BITS:0] rd_gray;
    (* ASYNC_REG = "TRUE" *) reg [ADDR_BITS:0] rd_gray_meta;
    (* ASYNC_REG = "TRUE" *) reg [ADDR_BITS:0] rd_gray_sync;
    (* ASYNC_REG = "TRUE" *) reg [ADDR_BITS:0] wr_gray_meta;
    (* ASYNC_REG = "TRUE" *) reg [ADDR_BITS:0] wr_gray_sync;
    wire [ADDR_BITS:0] wr_next = wr_binary + 1'b1;
    wire [ADDR_BITS:0] rd_next = rd_binary + 1'b1;
    wire empty = rd_gray == wr_gray_sync;

    assign wr_ready = wr_gray != {~rd_gray_sync[ADDR_BITS:ADDR_BITS-1],
                                  rd_gray_sync[ADDR_BITS-2:0]};

    always @(posedge wr_clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_binary <= 0;
            wr_gray <= 0;
            rd_gray_meta <= 0;
            rd_gray_sync <= 0;
        end else begin
            rd_gray_meta <= rd_gray;
            rd_gray_sync <= rd_gray_meta;
            if (wr_valid && wr_ready) begin
                wr_binary <= wr_next;
                wr_gray <= (wr_next >> 1) ^ wr_next;
            end
        end
    end

    always @(posedge wr_clk) begin
        if (wr_valid && wr_ready)
            memory[wr_binary[ADDR_BITS-1:0]] <= wr_data;
    end

    always @(posedge rd_clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_binary <= 0;
            rd_gray <= 0;
            wr_gray_meta <= 0;
            wr_gray_sync <= 0;
            rd_valid <= 1'b0;
            rd_data <= 0;
        end else begin
            wr_gray_meta <= wr_gray;
            wr_gray_sync <= wr_gray_meta;
            if (!rd_valid || rd_ready) begin
                rd_valid <= !empty;
                if (!empty) begin
                    rd_data <= memory[rd_binary[ADDR_BITS-1:0]];
                    rd_binary <= rd_next;
                    rd_gray <= (rd_next >> 1) ^ rd_next;
                end
            end
        end
    end
endmodule
