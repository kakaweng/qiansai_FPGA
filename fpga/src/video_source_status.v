`timescale 1ns / 1ps

module video_source_status #(
    parameter HDMI_RGB565 = 1,
    parameter CAMERA_DMA = 0,
    parameter ETHERNET_DMA = 0
)(
    input wire pcie_clk,
    input wire pcie_rst_n,
    input wire cam_clk,
    input wire cam_rst_n,
    input wire cam_configured,
    input wire cam_active,
    input wire cam_frame_seen,
    input wire cam_last_frame_valid,
    input wire [7:0] cam_error_flags,
    input wire [31:0] cam_good_frames,
    input wire [31:0] cam_bad_frames,
    input wire [31:0] cam_pixel_count,
    input wire [15:0] cam_line_count,
    input wire [15:0] cam_line_bytes,
    input wire [31:0] cam_pixel_sum,
    input wire [15:0] cam_first_pixel,
    input wire [15:0] cam_last_pixel,
    input wire [127:0] ethernet_status,
    output wire [895:0] info_data
);
    reg snapshot_request;
    reg snapshot_ack;
    (* ASYNC_REG = "TRUE" *) reg [1:0] request_sync;
    (* ASYNC_REG = "TRUE" *) reg [1:0] ack_sync;
    reg [255:0] snapshot_held;
    reg [255:0] snapshot_meta;
    reg [255:0] snapshot_sync;
    reg [255:0] snapshot_data;

    localparam [127:0] HDMI_DESCRIPTOR = {
        HDMI_RGB565 ? 32'd4147200 : 32'd2073600,
        HDMI_RGB565 ? 32'd2 : 32'd1,
        16'd1080, 16'd1920, 32'd0
    };
    localparam [127:0] CAMERA_DESCRIPTOR = {
        32'd614400, 32'd2, 16'd480, 16'd640, 32'd1
    };
    localparam [127:0] ETHERNET_DESCRIPTOR = {
        ETHERNET_DMA ? 32'd614400 : 32'd0,
        ETHERNET_DMA ? 32'd2 : 32'd0,
        ETHERNET_DMA ? 16'd480 : 16'd0,
        ETHERNET_DMA ? 16'd640 : 16'd0, 32'd2
    };

    localparam [127:0] DMA_CAPABILITIES = {
        32'd1, 32'h00000160,
        (CAMERA_DMA && HDMI_RGB565) ? (ETHERNET_DMA ? 32'd7 : 32'd3) : 32'd1,
        32'h4d565331
    };

    assign info_data = {ETHERNET_DMA ? ethernet_status : 128'd0, DMA_CAPABILITIES, snapshot_data,
                        ETHERNET_DESCRIPTOR, CAMERA_DESCRIPTOR, HDMI_DESCRIPTOR};

    always @(posedge cam_clk or negedge cam_rst_n) begin
        if (!cam_rst_n) begin
            request_sync <= 2'd0;
            snapshot_ack <= 1'b0;
            snapshot_held <= 256'd0;
        end else begin
            request_sync <= {request_sync[0], snapshot_request};
            if (!request_sync[1]) begin
                snapshot_ack <= 1'b0;
            end else if (!snapshot_ack) begin
                snapshot_held <= {
                    cam_good_frames + cam_bad_frames,
                    cam_last_pixel, cam_first_pixel,
                    cam_pixel_sum,
                    cam_line_bytes, cam_line_count,
                    cam_pixel_count,
                    cam_bad_frames,
                    cam_good_frames,
                    16'hc101, cam_error_flags, 4'd0,
                    cam_active, cam_last_frame_valid, cam_frame_seen, cam_configured
                };
                snapshot_ack <= 1'b1;
            end
        end
    end

    always @(posedge pcie_clk or negedge pcie_rst_n) begin
        if (!pcie_rst_n) begin
            snapshot_request <= 1'b0;
            ack_sync <= 2'd0;
            snapshot_meta <= 256'd0;
            snapshot_sync <= 256'd0;
            snapshot_data <= 256'd0;
        end else begin
            ack_sync <= {ack_sync[0], snapshot_ack};
            snapshot_meta <= snapshot_held;
            snapshot_sync <= snapshot_meta;
            if (!snapshot_request && !ack_sync[1]) begin
                snapshot_request <= 1'b1;
            end else if (snapshot_request && ack_sync[1]) begin
                snapshot_data <= snapshot_sync;
                snapshot_request <= 1'b0;
            end
        end
    end
endmodule
