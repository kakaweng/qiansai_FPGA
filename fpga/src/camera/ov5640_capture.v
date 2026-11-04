`timescale 1ns / 1ps

module ov5640_capture #(
    parameter FRAME_WIDTH = 640,
    parameter FRAME_HEIGHT = 480,
    parameter VSYNC_ACTIVE_HIGH = 1,
    parameter HIGH_BYTE_FIRST = 1
)(
    input wire pclk,
    input wire rst_n,
    input wire init_done,
    input wire vsync,
    input wire href,
    input wire [7:0] data,
    output wire configured,
    output reg capture_active,
    output reg frame_start,
    output reg frame_end,
    output reg line_start,
    output reg line_end,
    output reg pixel_valid,
    output reg [15:0] pixel_data,
    output reg frame_valid,
    output reg frame_error,
    output reg frame_seen,
    output reg last_frame_valid,
    output reg [7:0] last_error_flags,
    output reg [31:0] good_frames,
    output reg [31:0] bad_frames,
    output reg [31:0] last_pixel_count,
    output reg [15:0] last_line_count,
    output reg [15:0] last_line_bytes,
    output reg [31:0] last_pixel_sum,
    output reg [15:0] last_first_pixel,
    output reg [15:0] last_last_pixel
);
    localparam LINE_BYTES = FRAME_WIDTH * 2;
    localparam FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT;

    (* ASYNC_REG = "TRUE" *) reg [1:0] init_sync;
    reg vsync_previous;
    reg href_previous;
    reg byte_pending;
    reg [7:0] first_byte;
    reg [15:0] line_bytes;
    reg [15:0] completed_lines;
    reg [31:0] pixel_count;
    reg [31:0] pixel_sum;
    reg [15:0] first_pixel;
    reg [15:0] last_pixel;
    reg [15:0] completed_line_bytes;
    reg [7:0] error_flags;

    wire vsync_active = VSYNC_ACTIVE_HIGH ? vsync : ~vsync;
    wire [15:0] paired_pixel = HIGH_BYTE_FIRST ? {first_byte, data} : {data, first_byte};
    wire [7:0] line_errors = {6'd0, (line_bytes != LINE_BYTES), byte_pending};
    wire [15:0] ending_lines = completed_lines + (href_previous ? 16'd1 : 16'd0);
    wire [7:0] ending_errors = error_flags |
        (href_previous ? line_errors : 8'd0) |
        ((ending_lines != FRAME_HEIGHT || pixel_count != FRAME_PIXELS) ? 8'h04 : 8'd0) |
        (href ? 8'h08 : 8'd0);

    assign configured = init_sync[1];

    always @(posedge pclk or negedge rst_n) begin
        if (!rst_n)
            init_sync <= 2'd0;
        else
            init_sync <= {init_sync[0], init_done};
    end

    always @(posedge pclk or negedge rst_n) begin
        if (!rst_n) begin
            vsync_previous <= 1'b0;
            href_previous <= 1'b0;
            capture_active <= 1'b0;
            frame_start <= 1'b0;
            frame_end <= 1'b0;
            line_start <= 1'b0;
            line_end <= 1'b0;
            pixel_valid <= 1'b0;
            pixel_data <= 16'd0;
            frame_valid <= 1'b0;
            frame_error <= 1'b0;
            frame_seen <= 1'b0;
            last_frame_valid <= 1'b0;
            last_error_flags <= 8'd0;
            good_frames <= 32'd0;
            bad_frames <= 32'd0;
            last_pixel_count <= 32'd0;
            last_line_count <= 16'd0;
            last_line_bytes <= 16'd0;
            last_pixel_sum <= 32'd0;
            last_first_pixel <= 16'd0;
            last_last_pixel <= 16'd0;
            byte_pending <= 1'b0;
            first_byte <= 8'd0;
            line_bytes <= 16'd0;
            completed_lines <= 16'd0;
            pixel_count <= 32'd0;
            pixel_sum <= 32'd0;
            first_pixel <= 16'd0;
            last_pixel <= 16'd0;
            completed_line_bytes <= 16'd0;
            error_flags <= 8'd0;
        end else begin
            vsync_previous <= vsync_active;
            href_previous <= href;
            frame_start <= 1'b0;
            frame_end <= 1'b0;
            line_start <= 1'b0;
            line_end <= 1'b0;
            pixel_valid <= 1'b0;
            frame_valid <= 1'b0;
            frame_error <= 1'b0;

            if (!configured) begin
                capture_active <= 1'b0;
                byte_pending <= 1'b0;
            end else if (vsync_active) begin
                capture_active <= 1'b0;
                byte_pending <= 1'b0;
                if (capture_active) begin
                    frame_end <= 1'b1;
                    line_end <= href_previous;
                    frame_seen <= 1'b1;
                    last_frame_valid <= (ending_errors == 8'd0);
                    last_error_flags <= ending_errors;
                    last_pixel_count <= pixel_count;
                    last_line_count <= ending_lines;
                    last_line_bytes <= href_previous ? line_bytes : completed_line_bytes;
                    last_pixel_sum <= pixel_sum;
                    last_first_pixel <= first_pixel;
                    last_last_pixel <= last_pixel;
                    if (ending_errors == 8'd0) begin
                        frame_valid <= 1'b1;
                        good_frames <= good_frames + 32'd1;
                    end else begin
                        frame_error <= 1'b1;
                        bad_frames <= bad_frames + 32'd1;
                    end
                end
            end else if (vsync_previous) begin
                capture_active <= 1'b1;
                frame_start <= 1'b1;
                byte_pending <= 1'b0;
                line_bytes <= 16'd0;
                completed_lines <= 16'd0;
                pixel_count <= 32'd0;
                pixel_sum <= 32'd0;
                first_pixel <= 16'd0;
                last_pixel <= 16'd0;
                completed_line_bytes <= 16'd0;
                error_flags <= href ? 8'h08 : 8'd0;
            end else if (capture_active) begin
                if (href) begin
                    if (!href_previous) begin
                        line_start <= 1'b1;
                        line_bytes <= 16'd1;
                        first_byte <= data;
                        byte_pending <= 1'b1;
                        if (completed_lines >= FRAME_HEIGHT)
                            error_flags <= error_flags | 8'h04;
                    end else begin
                        if (line_bytes != 16'hffff)
                            line_bytes <= line_bytes + 16'd1;
                        else
                            error_flags <= error_flags | 8'h02;
                        byte_pending <= ~byte_pending;
                        if (!byte_pending) begin
                            first_byte <= data;
                        end else begin
                            pixel_data <= paired_pixel;
                            pixel_valid <= (line_bytes < LINE_BYTES && completed_lines < FRAME_HEIGHT);
                            pixel_count <= pixel_count + 32'd1;
                            pixel_sum <= pixel_sum + {16'd0, paired_pixel};
                            if (pixel_count == 32'd0)
                                first_pixel <= paired_pixel;
                            last_pixel <= paired_pixel;
                        end
                    end
                end else if (href_previous) begin
                    line_end <= 1'b1;
                    byte_pending <= 1'b0;
                    if (completed_lines != 16'hffff)
                        completed_lines <= completed_lines + 16'd1;
                    completed_line_bytes <= line_bytes;
                    error_flags <= error_flags | line_errors |
                                   (completed_lines == 16'hffff ? 8'h04 : 8'd0);
                end
            end
        end
    end
endmodule
