`timescale 1ns / 1ps

module video_status_snapshot (
    input wire source_clk,
    input wire source_rst_n,
    input wire [127:0] source_data,
    input wire destination_clk,
    input wire destination_rst_n,
    output reg [127:0] destination_data
);
    reg request, acknowledge;
    (* ASYNC_REG = "TRUE" *) reg [1:0] request_sync, acknowledge_sync;
    reg [127:0] held;
    reg [2:0] settle;
    always @(posedge source_clk or negedge source_rst_n) begin
        if (!source_rst_n) begin
            request_sync <= 0;
            acknowledge <= 0;
            held <= 0;
        end else begin
            request_sync <= {request_sync[0], request};
            if (request_sync[1] != acknowledge) begin
                held <= source_data;
                acknowledge <= request_sync[1];
            end
        end
    end
    always @(posedge destination_clk or negedge destination_rst_n) begin
        if (!destination_rst_n) begin
            request <= 0;
            acknowledge_sync <= 0;
            destination_data <= 0;
            settle <= 0;
        end else begin
            acknowledge_sync <= {acknowledge_sync[0], acknowledge};
            if (request != acknowledge_sync[1]) settle <= 0;
            else if (settle == 4) begin
                destination_data <= held;
                request <= ~request;
                settle <= 0;
            end else settle <= settle + 1'b1;
        end
    end
endmodule
