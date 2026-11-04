`timescale 1ns / 1ps

module udp_rgb_test_pkt #(
    parameter OUT_WIDTH     = 640,
    parameter OUT_HEIGHT    = 480,
    parameter X_START_SKIP  = 0,
    parameter Y_START_SKIP  = 60,
    parameter X_SAMPLE_STEP = 3,
    parameter Y_SAMPLE_STEP = 2,
    parameter SEG_PIXELS    = 480,
    parameter INTER_PKT_GAP = 128
)(
    input  wire        capture_req_toggle,

    input  wire        pix_clk,
    input  wire        pix_rst_n,
    input  wire        pix_vs,
    input  wire        pix_de,
    input  wire [7:0]  pix_r,
    input  wire [7:0]  pix_g,
    input  wire [7:0]  pix_b,

    input  wire        eth_clk,
    input  wire        eth_rst_n,
    input  wire        udp_tx_done,
    input  wire        udp_tx_req,
    output reg         udp_tx_start_en,
    output reg  [7:0]  udp_tx_data,
    output wire [15:0] udp_tx_byte_num,
    output wire        capture_busy
);

    localparam [15:0] HEADER_LEN      = 16'd14;
    localparam [15:0] OUT_WIDTH_U16   = OUT_WIDTH;
    localparam [15:0] OUT_HEIGHT_U16  = OUT_HEIGHT;
    localparam [15:0] SEG_PIXELS_U16  = SEG_PIXELS;
    localparam [15:0] SEG_COUNT_U16   = (OUT_WIDTH + SEG_PIXELS - 1) / SEG_PIXELS;
    localparam [15:0] X_SKIP_U16      = X_START_SKIP;
    localparam [15:0] Y_SKIP_U16      = Y_START_SKIP;
    localparam [15:0] X_STEP_U16      = X_SAMPLE_STEP;
    localparam [15:0] Y_STEP_U16      = Y_SAMPLE_STEP;
    localparam [15:0] X_CAPTURE_END   = X_SKIP_U16 + OUT_WIDTH_U16 * X_STEP_U16;
    localparam [15:0] Y_CAPTURE_END   = Y_SKIP_U16 + OUT_HEIGHT_U16 * Y_STEP_U16;
    localparam [15:0] MAX_PAYLOAD_LEN = SEG_PIXELS_U16 * 16'd3;

    reg [23:0] line_buf0 [0:OUT_WIDTH-1];
    reg [23:0] line_buf1 [0:OUT_WIDTH-1];

    reg        pix_vs_d;
    reg        pix_de_d;
    reg        capture_active;
    reg        capture_pending;
    reg        selected_line;
    reg        wr_buf_sel;
    reg        row_toggle_pix;
    reg        row_done_buf_sel;
    reg [2:0]  capture_req_sync;
    reg        capture_req_seen;
    reg [7:0]  frame_id_pix;
    reg [15:0] row_done_id_pix;
    reg [7:0]  row_done_frame_pix;
    reg [15:0] src_x;
    reg [15:0] src_y;
    reg [15:0] sample_x;
    reg [15:0] sample_y;
    reg [15:0] x_step_cnt;
    reg [15:0] y_step_cnt;
    reg [2:0]  send_active_sync;

    wire       pix_vs_rise;
    wire       pix_de_rise;
    wire       pix_de_fall;
    wire       in_y_window;
    wire       send_active_pix;

    reg [2:0]  row_toggle_sync;
    reg        row_toggle_eth;
    reg        tx_busy;
    reg [15:0] gap_cnt;
    reg        pending_valid;
    reg        pending_buf_sel;
    reg [15:0] pending_row_id;
    reg [7:0]  pending_frame_id;
    reg        active_buf_sel;
    reg [15:0] active_row_id;
    reg [7:0]  active_frame_id;
    reg [15:0] active_x_offset;
    reg [15:0] active_payload_pixels;
    reg [15:0] active_payload_len;
    reg [15:0] segment_id;
    reg        tx_req_d;
    reg        tx_req_skip;
    reg [15:0] rd_addr;
    reg [15:0] tx_pixel_addr;
    reg [1:0]  tx_color_sel;

    wire [15:0] segment_x_offset;
    wire [15:0] segment_remain_pixels;
    wire [15:0] payload_rd_offset;
    wire [15:0] line_rd_addr;
    wire        payload_rd_valid;
    wire        send_active_eth;
    wire [23:0] active_rgb;

    assign udp_tx_byte_num = HEADER_LEN + active_payload_len;
    assign pix_vs_rise = pix_vs && !pix_vs_d;
    assign pix_de_rise = pix_de && !pix_de_d;
    assign pix_de_fall = !pix_de && pix_de_d;
    assign in_y_window = (src_y >= Y_SKIP_U16) && (src_y < Y_CAPTURE_END);
    assign segment_x_offset = segment_id * SEG_PIXELS_U16;
    assign segment_remain_pixels = OUT_WIDTH_U16 - segment_x_offset;
    assign payload_rd_offset = rd_addr - HEADER_LEN;
    assign line_rd_addr = tx_pixel_addr;
    assign payload_rd_valid = payload_rd_offset < active_payload_len;
    assign send_active_eth = pending_valid || tx_busy || (gap_cnt != 16'd0);
    assign send_active_pix = send_active_sync[2];
    assign capture_busy = capture_active || capture_pending || send_active_pix;
    assign active_rgb = active_buf_sel ? line_buf1[line_rd_addr] : line_buf0[line_rd_addr];

    always @(posedge pix_clk or negedge pix_rst_n) begin
        if(!pix_rst_n) begin
            pix_vs_d           <= 1'b0;
            pix_de_d           <= 1'b0;
            capture_active     <= 1'b0;
            capture_pending    <= 1'b0;
            selected_line      <= 1'b0;
            wr_buf_sel         <= 1'b0;
            row_toggle_pix     <= 1'b0;
            row_done_buf_sel   <= 1'b0;
            capture_req_sync   <= 3'd0;
            capture_req_seen   <= 1'b0;
            frame_id_pix       <= 8'd0;
            row_done_id_pix    <= 16'd0;
            row_done_frame_pix <= 8'd0;
            src_x              <= 16'd0;
            src_y              <= 16'd0;
            sample_x           <= 16'd0;
            sample_y           <= 16'd0;
            x_step_cnt         <= 16'd0;
            y_step_cnt         <= 16'd0;
            send_active_sync   <= 3'd0;
        end
        else begin
            pix_vs_d <= pix_vs;
            pix_de_d <= pix_de;
            capture_req_sync <= {capture_req_sync[1:0], capture_req_toggle};
            send_active_sync <= {send_active_sync[1:0], send_active_eth};

            if(capture_req_sync[2] != capture_req_seen) begin
                capture_req_seen <= capture_req_sync[2];
                if(!capture_active && !capture_pending && !send_active_pix)
                    capture_pending <= 1'b1;
            end

            if(pix_vs_rise) begin
                selected_line <= 1'b0;
                wr_buf_sel    <= 1'b0;
                src_x         <= 16'd0;
                src_y         <= 16'd0;
                sample_x      <= 16'd0;
                sample_y      <= 16'd0;
                x_step_cnt    <= 16'd0;
                y_step_cnt    <= 16'd0;

                if(capture_pending && !capture_active) begin
                    capture_pending <= 1'b0;
                    capture_active  <= 1'b1;
                    frame_id_pix    <= frame_id_pix + 1'b1;
                end
            end
            else if(capture_active) begin
                if(pix_de_rise) begin
                    src_x <= 16'd0;
                    x_step_cnt <= 16'd0;
                    sample_x <= 16'd0;
                    selected_line <= in_y_window &&
                                     (y_step_cnt == 16'd0) &&
                                     (sample_y < OUT_HEIGHT_U16);
                end
                else if(pix_de) begin
                    if(selected_line &&
                       (src_x >= X_SKIP_U16) &&
                       (src_x < X_CAPTURE_END) &&
                       (x_step_cnt == 16'd0) &&
                       (sample_x < OUT_WIDTH_U16)) begin
                        if(wr_buf_sel)
                            line_buf1[sample_x] <= {pix_r, pix_g, pix_b};
                        else
                            line_buf0[sample_x] <= {pix_r, pix_g, pix_b};

                        sample_x <= sample_x + 1'b1;
                    end

                    if(src_x >= X_SKIP_U16 && src_x < X_CAPTURE_END) begin
                        if(x_step_cnt >= X_STEP_U16 - 1'b1)
                            x_step_cnt <= 16'd0;
                        else
                            x_step_cnt <= x_step_cnt + 1'b1;
                    end

                    src_x <= src_x + 1'b1;
                end
                else if(pix_de_fall) begin
                    if(selected_line) begin
                        row_done_buf_sel   <= wr_buf_sel;
                        row_done_id_pix    <= sample_y;
                        row_done_frame_pix <= frame_id_pix;
                        row_toggle_pix     <= ~row_toggle_pix;
                        wr_buf_sel         <= ~wr_buf_sel;

                        if(sample_y >= OUT_HEIGHT_U16 - 1'b1)
                            capture_active <= 1'b0;
                        else
                            sample_y <= sample_y + 1'b1;
                    end

                    selected_line <= 1'b0;

                    if(in_y_window) begin
                        if(y_step_cnt >= Y_STEP_U16 - 1'b1)
                            y_step_cnt <= 16'd0;
                        else
                            y_step_cnt <= y_step_cnt + 1'b1;
                    end

                    src_y <= src_y + 1'b1;
                end
            end
        end
    end

    always @(posedge eth_clk or negedge eth_rst_n) begin
        if(!eth_rst_n) begin
            row_toggle_sync <= 3'd0;
            row_toggle_eth  <= 1'b0;
        end
        else begin
            row_toggle_sync <= {row_toggle_sync[1:0], row_toggle_pix};
            row_toggle_eth  <= row_toggle_sync[2];
        end
    end

    always @(posedge eth_clk or negedge eth_rst_n) begin
        if(!eth_rst_n) begin
            pending_valid    <= 1'b0;
            pending_buf_sel  <= 1'b0;
            pending_row_id   <= 16'd0;
            pending_frame_id <= 8'd0;
            active_buf_sel   <= 1'b0;
            active_row_id    <= 16'd0;
            active_frame_id  <= 8'd0;
            active_x_offset  <= 16'd0;
            active_payload_pixels <= 16'd0;
            active_payload_len <= 16'd0;
            segment_id       <= 16'd0;
            tx_busy          <= 1'b0;
            gap_cnt          <= 16'd0;
            udp_tx_start_en  <= 1'b0;
        end
        else begin
            udp_tx_start_en <= 1'b0;

            if(row_toggle_sync[2] != row_toggle_eth) begin
                pending_valid    <= 1'b1;
                pending_buf_sel  <= row_done_buf_sel;
                pending_row_id   <= row_done_id_pix;
                pending_frame_id <= row_done_frame_pix;
                segment_id       <= 16'd0;
            end

            if(udp_tx_done) begin
                tx_busy <= 1'b0;
                if(segment_id < SEG_COUNT_U16)
                    segment_id <= segment_id + 1'b1;
                gap_cnt <= INTER_PKT_GAP[15:0];
            end
            else if(gap_cnt != 16'd0) begin
                gap_cnt <= gap_cnt - 1'b1;
            end

            if(pending_valid && !tx_busy && (gap_cnt == 16'd0) && (segment_id < SEG_COUNT_U16)) begin
                if(segment_id >= SEG_COUNT_U16 - 1'b1)
                    pending_valid <= 1'b0;

                active_buf_sel  <= pending_buf_sel;
                active_row_id   <= pending_row_id;
                active_frame_id <= pending_frame_id;
                active_x_offset <= segment_x_offset;
                if(segment_remain_pixels > SEG_PIXELS_U16)
                    active_payload_pixels <= SEG_PIXELS_U16;
                else
                    active_payload_pixels <= segment_remain_pixels;

                if(segment_remain_pixels > SEG_PIXELS_U16)
                    active_payload_len <= SEG_PIXELS_U16 * 16'd3;
                else
                    active_payload_len <= segment_remain_pixels * 16'd3;

                tx_busy         <= 1'b1;
                udp_tx_start_en <= 1'b1;
            end
        end
    end

    always @(posedge eth_clk or negedge eth_rst_n) begin
        if(!eth_rst_n) begin
            tx_req_d      <= 1'b0;
            tx_req_skip   <= 1'b0;
            rd_addr       <= 16'd0;
            tx_pixel_addr <= 16'd0;
            tx_color_sel  <= 2'd0;
        end
        else begin
            tx_req_d <= udp_tx_req;

            if(udp_tx_start_en) begin
                tx_req_skip   <= 1'b0;
                rd_addr       <= 16'd0;
                tx_pixel_addr <= active_x_offset;
                tx_color_sel  <= 2'd0;
            end
            else if(udp_tx_req && !tx_req_d) begin
                tx_req_skip   <= 1'b1;
                rd_addr       <= 16'd0;
                tx_pixel_addr <= active_x_offset;
                tx_color_sel  <= 2'd0;
            end
            else if(udp_tx_req) begin
                if(tx_req_skip) begin
                    tx_req_skip   <= 1'b0;
                    rd_addr       <= 16'd1;
                    tx_pixel_addr <= active_x_offset;
                    tx_color_sel  <= 2'd0;
                end
                else if(rd_addr < udp_tx_byte_num - 1'b1) begin
                    rd_addr <= rd_addr + 1'b1;
                    if(rd_addr >= HEADER_LEN) begin
                        if(tx_color_sel == 2'd2) begin
                            tx_color_sel <= 2'd0;
                            tx_pixel_addr <= tx_pixel_addr + 16'd1;
                        end
                        else begin
                            tx_color_sel <= tx_color_sel + 1'b1;
                        end
                    end
                end
            end
        end
    end

    always @(*) begin
        case(rd_addr)
            16'd0: udp_tx_data = 8'h49;
            16'd1: udp_tx_data = 8'h4d;
            16'd2: udp_tx_data = active_frame_id;
            16'd3: udp_tx_data = 8'h03;
            16'd4: udp_tx_data = active_row_id[7:0];
            16'd5: udp_tx_data = active_row_id[15:8];
            16'd6: udp_tx_data = OUT_WIDTH_U16[7:0];
            16'd7: udp_tx_data = OUT_WIDTH_U16[15:8];
            16'd8: udp_tx_data = OUT_HEIGHT_U16[7:0];
            16'd9: udp_tx_data = OUT_HEIGHT_U16[15:8];
            16'd10: udp_tx_data = active_x_offset[7:0];
            16'd11: udp_tx_data = active_x_offset[15:8];
            16'd12: udp_tx_data = active_payload_len[7:0];
            16'd13: udp_tx_data = active_payload_len[15:8];
            default: begin
                if(!payload_rd_valid)
                    udp_tx_data = 8'd0;
                else if(tx_color_sel == 2'd0)
                    udp_tx_data = active_rgb[23:16];
                else if(tx_color_sel == 2'd1)
                    udp_tx_data = active_rgb[15:8];
                else
                    udp_tx_data = active_rgb[7:0];
            end
        endcase
    end

endmodule
