`timescale 1ns / 1ps

module camera_ddr_snapshot #(
    parameter ADDR_WIDTH = 28,
    parameter FRAME_WIDTH = 640,
    parameter FRAME_HEIGHT = 480,
    parameter FRAME_BASE = 28'h0800000,
    parameter FIFO_ADDR_BITS = 7,
    parameter PREFETCH = 0
)(
    input wire rst_n,
    input wire cam_clk,
    input wire frame_start,
    input wire frame_end,
    input wire frame_valid,
    input wire pixel_valid,
    input wire [15:0] pixel_data,
    input wire ddr_clk,
    input wire capture_request,
    output reg frame_ready,
    output reg [31:0] frames_captured,
    output reg [31:0] frames_rejected,
    output reg [ADDR_WIDTH-1:0] axi_awaddr,
    output wire [3:0] axi_awid,
    output wire [3:0] axi_awlen,
    output wire [2:0] axi_awsize,
    output wire [1:0] axi_awburst,
    output wire axi_awvalid,
    input wire axi_awready,
    output reg [255:0] axi_wdata,
    output wire [31:0] axi_wstrb,
    output wire axi_wvalid,
    input wire axi_wready
);
    localparam [1:0] TOKEN_START = 0, TOKEN_DATA = 1, TOKEN_END = 2;
    localparam [2:0] CAM_IDLE = 0, CAM_ARM = 1, CAM_PIXELS = 2,
                     CAM_END = 3, CAM_HOLD = 4;
    localparam [1:0] WRITE_IDLE = 0, WRITE_ADDR = 1, WRITE_DATA = 2;
    localparam FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT;
    localparam FRAME_WORDS = FRAME_PIXELS / 16;

    reg request_previous;
    reg request_epoch;
    (* ASYNC_REG = "TRUE" *) reg [1:0] request_sync;
    (* ASYNC_REG = "TRUE" *) reg [1:0] epoch_sync;
    reg epoch_seen;
    reg cam_epoch;
    reg [2:0] cam_state;
    reg [255:0] packed_pixels;
    reg [3:0] pixel_index;
    reg [31:0] pixel_count;
    reg overflow;
    reg end_good;
    wire fifo_wr_ready;
    wire fifo_wr_valid;
    wire [258:0] fifo_wr_data;
    wire fifo_rd_valid;
    wire fifo_rd_ready;
    wire [258:0] fifo_rd_data;
    reg [1:0] write_state;
    reg write_epoch;
    reg frame_writing;
    reg [31:0] words_written;
    wire new_request = capture_request && !request_previous;
    wire current_token = PREFETCH ||
                         (capture_request && fifo_rd_data[258] == request_epoch);
    wire freeze_frame = PREFETCH && end_good && request_sync[1];

    assign fifo_wr_valid = (PREFETCH || (request_sync[1] && epoch_sync[1] == epoch_seen)) &&
        ((cam_state == CAM_ARM && frame_start) || cam_state == CAM_END ||
         (cam_state == CAM_PIXELS && pixel_valid && pixel_index == 15 && !overflow));
    assign fifo_wr_data = cam_state == CAM_ARM ? {cam_epoch, TOKEN_START, 256'd0} :
                          cam_state == CAM_END ? {PREFETCH ? epoch_sync[1] : cam_epoch,
                              TOKEN_END, 254'd0, freeze_frame, end_good} :
                          {cam_epoch, TOKEN_DATA, pixel_data, packed_pixels[239:0]};
    assign fifo_rd_ready = write_state == WRITE_IDLE;
    assign axi_awid = 4'd1;
    assign axi_awlen = 4'd0;
    assign axi_awsize = 3'b110;
    assign axi_awburst = 2'b01;
    assign axi_awvalid = write_state == WRITE_ADDR;
    assign axi_wstrb = 32'hffffffff;
    assign axi_wvalid = write_state == WRITE_DATA;

    video_async_fifo #(.DATA_WIDTH(259), .ADDR_BITS(FIFO_ADDR_BITS)) u_pixels (
        .rst_n(rst_n), .wr_clk(cam_clk), .wr_valid(fifo_wr_valid),
        .wr_ready(fifo_wr_ready), .wr_data(fifo_wr_data),
        .rd_clk(ddr_clk), .rd_valid(fifo_rd_valid),
        .rd_ready(fifo_rd_ready), .rd_data(fifo_rd_data)
    );

    always @(posedge cam_clk or negedge rst_n) begin
        if (!rst_n) begin
            request_sync <= 0;
            epoch_sync <= 0;
            epoch_seen <= 0;
            cam_epoch <= 0;
            cam_state <= PREFETCH ? CAM_ARM : CAM_IDLE;
            packed_pixels <= 0;
            pixel_index <= 0;
            pixel_count <= 0;
            overflow <= 0;
            end_good <= 0;
        end else begin
            request_sync <= {request_sync[0], capture_request};
            epoch_sync <= {epoch_sync[0], request_epoch};
            if (!PREFETCH && !request_sync[1]) begin
                cam_state <= CAM_IDLE;
                pixel_index <= 0;
            end else if (!PREFETCH && epoch_sync[1] != epoch_seen) begin
                epoch_seen <= epoch_sync[1];
                cam_epoch <= epoch_sync[1];
                cam_state <= CAM_ARM;
                pixel_index <= 0;
                pixel_count <= 0;
                overflow <= 0;
            end else begin
                case (cam_state)
                    CAM_ARM: begin
                        if (frame_start && fifo_wr_ready) begin
                            cam_state <= CAM_PIXELS;
                            pixel_index <= 0;
                            pixel_count <= 0;
                            overflow <= 0;
                        end
                    end
                    CAM_PIXELS: begin
                        if (pixel_valid) begin
                            packed_pixels[pixel_index*16 +: 16] <= pixel_data;
                            pixel_index <= pixel_index + 1'b1;
                            pixel_count <= pixel_count + 1'b1;
                            if (pixel_index == 15 && !fifo_wr_ready)
                                overflow <= 1'b1;
                        end
                        if (frame_end) begin
                            end_good <= frame_valid && !overflow &&
                                        pixel_count == FRAME_PIXELS && pixel_index == 0;
                            cam_state <= CAM_END;
                        end
                    end
                    CAM_END: begin
                        if (fifo_wr_ready) begin
                            if (PREFETCH) begin
                                cam_state <= freeze_frame ? CAM_HOLD : CAM_ARM;
                                if (freeze_frame)
                                    cam_epoch <= epoch_sync[1];
                            end else begin
                                cam_state <= end_good ? CAM_HOLD : CAM_ARM;
                            end
                        end
                    end
                    CAM_HOLD: begin
                        if (PREFETCH && (!request_sync[1] || epoch_sync[1] != cam_epoch))
                            cam_state <= CAM_ARM;
                    end
                    default: begin end
                endcase
            end
        end
    end

    always @(posedge ddr_clk or negedge rst_n) begin
        if (!rst_n) begin
            request_previous <= 0;
            request_epoch <= 0;
            frame_ready <= 0;
            frames_captured <= 0;
            frames_rejected <= 0;
            write_state <= WRITE_IDLE;
            write_epoch <= 0;
            frame_writing <= 0;
            words_written <= 0;
            axi_awaddr <= 0;
            axi_wdata <= 0;
        end else begin
            request_previous <= capture_request;
            case (write_state)
                WRITE_IDLE: begin
                    if (fifo_rd_valid && current_token && !frame_ready) begin
                        case (fifo_rd_data[257:256])
                            TOKEN_START: begin
                                frame_writing <= 1'b1;
                                words_written <= 0;
                            end
                            TOKEN_DATA: begin
                                if (frame_writing && words_written < FRAME_WORDS) begin
                                    axi_awaddr <= FRAME_BASE + (words_written << 3);
                                    axi_wdata <= fifo_rd_data[255:0];
                                    write_epoch <= fifo_rd_data[258];
                                    write_state <= WRITE_ADDR;
                                end
                            end
                            TOKEN_END: begin
                                frame_writing <= 1'b0;
                                if (frame_writing && fifo_rd_data[0] && words_written == FRAME_WORDS) begin
                                    if (!PREFETCH || (capture_request && fifo_rd_data[1] &&
                                        fifo_rd_data[258] == request_epoch)) begin
                                        frame_ready <= 1'b1;
                                        frames_captured <= frames_captured + 1'b1;
                                    end
                                end else begin
                                    frames_rejected <= frames_rejected + 1'b1;
                                end
                            end
                            default: frame_writing <= 1'b0;
                        endcase
                    end
                end
                WRITE_ADDR: begin
                    if (axi_awready)
                        write_state <= WRITE_DATA;
                end
                WRITE_DATA: begin
                    if (axi_wready) begin
                        if (PREFETCH || (capture_request && write_epoch == request_epoch))
                            words_written <= words_written + 1'b1;
                        write_state <= WRITE_IDLE;
                    end
                end
                default: write_state <= WRITE_IDLE;
            endcase
            if (!capture_request) begin
                frame_ready <= 1'b0;
                if (!PREFETCH)
                    frame_writing <= 1'b0;
            end
            if (new_request) begin
                request_epoch <= ~request_epoch;
                frame_ready <= 1'b0;
                if (!PREFETCH) begin
                    frame_writing <= 1'b0;
                    words_written <= 0;
                end
            end
        end
    end
endmodule
