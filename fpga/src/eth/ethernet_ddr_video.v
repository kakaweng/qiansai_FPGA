`timescale 1ns / 1ps

module ethernet_ddr_video #(
    parameter ADDR_WIDTH = 28,
    parameter FRAME_BYTES = 614400,
    parameter FRAME_BASE = 28'h0900000,
    parameter SLOT_STRIDE = 28'h0040000
)(
    input wire rst_n,
    input wire rx_clk,
    input wire rx_en,
    input wire [7:0] rx_data,
    input wire rx_done,
    input wire [15:0] rx_bytes,
    input wire ddr_clk,
    input wire capture_request,
    output reg frame_ready,
    output wire [ADDR_WIDTH-1:0] read_base,
    output wire [127:0] status,
    output reg [ADDR_WIDTH-1:0] axi_awaddr,
    output wire axi_awvalid,
    input wire axi_awready,
    output reg [255:0] axi_wdata,
    output wire axi_wvalid,
    input wire axi_wready
);
    wire token_valid, token_ready;
    wire [258:0] token_data;
    wire fifo_valid, fifo_ready;
    wire [258:0] fifo_data;
    wire [31:0] packets_good, packets_bad;
    reg [1:0] write_state;
    reg writing, published_valid;
    reg [1:0] write_slot, published_slot, read_slot;
    reg [31:0] words_written, write_frame, published_frame, frames_good, frames_bad;
    reg [1:0] free_slot;
    integer slot;

    ethernet_video_rx #(.FRAME_BYTES(FRAME_BYTES)) u_receiver (
        .clk(rx_clk), .rst_n(rst_n), .rx_en(rx_en), .rx_data(rx_data),
        .rx_done(rx_done), .rx_bytes(rx_bytes), .token_valid(token_valid),
        .token_ready(token_ready), .token_data(token_data),
        .packets_good(packets_good), .packets_bad(packets_bad)
    );
    video_async_fifo #(.DATA_WIDTH(259), .ADDR_BITS(5), .RAM_STYLE("select_ram")) u_tokens (
        .rst_n(rst_n), .wr_clk(rx_clk), .wr_valid(token_valid),
        .wr_ready(token_ready), .wr_data(token_data), .rd_clk(ddr_clk),
        .rd_valid(fifo_valid), .rd_ready(fifo_ready), .rd_data(fifo_data)
    );

    always @(*) begin
        free_slot = 0;
        for (slot = 0; slot < 3; slot = slot + 1)
            if ((!published_valid || published_slot != slot) &&
                (!frame_ready || read_slot != slot)) free_slot = slot;
    end
    assign fifo_ready = write_state == 0;
    assign axi_awvalid = write_state == 1;
    assign axi_wvalid = write_state == 2;
    assign read_base = FRAME_BASE + read_slot * SLOT_STRIDE;
    assign status = {frames_bad, frames_good, published_frame, 30'd0, frame_ready, published_valid};

    always @(posedge ddr_clk or negedge rst_n) begin
        if (!rst_n) begin
            write_state <= 0;
            writing <= 0;
            published_valid <= 0;
            write_slot <= 0;
            published_slot <= 0;
            read_slot <= 0;
            frame_ready <= 0;
            words_written <= 0;
            write_frame <= 0;
            published_frame <= 0;
            frames_good <= 0;
            frames_bad <= 0;
            axi_awaddr <= 0;
            axi_wdata <= 0;
        end else begin
            if (!capture_request) frame_ready <= 0;
            else if (!frame_ready && published_valid) begin
                frame_ready <= 1;
                read_slot <= published_slot;
            end
            case (write_state)
                0: if (fifo_valid) begin
                    case (fifo_data[257:256])
                        0: begin
                            if (writing) frames_bad <= frames_bad + 1'b1;
                            writing <= 1;
                            words_written <= 0;
                            write_frame <= fifo_data[31:0];
                            write_slot <= free_slot;
                        end
                        1: if (writing && words_written < FRAME_BYTES / 32) begin
                            axi_awaddr <= FRAME_BASE + write_slot * SLOT_STRIDE + (words_written << 3);
                            axi_wdata <= fifo_data[255:0];
                            write_state <= 1;
                        end
                        2: begin
                            writing <= 0;
                            if (writing && words_written == FRAME_BYTES / 32 && fifo_data[31:0] == write_frame) begin
                                published_valid <= 1;
                                published_slot <= write_slot;
                                published_frame <= write_frame;
                                frames_good <= frames_good + 1'b1;
                            end else frames_bad <= frames_bad + 1'b1;
                        end
                        default: begin
                            writing <= 0;
                            frames_bad <= frames_bad + 1'b1;
                        end
                    endcase
                end
                1: if (axi_awready) write_state <= 2;
                2: if (axi_wready) begin
                    words_written <= words_written + 1'b1;
                    write_state <= 0;
                end
                default: write_state <= 0;
            endcase
        end
    end
endmodule
