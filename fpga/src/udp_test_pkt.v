`timescale 1ns / 1ps

module udp_test_pkt #(
    parameter OUT_WIDTH     = 960,
    parameter OUT_HEIGHT    = 540,
    parameter X_START_SKIP  = 0,
    parameter Y_START_SKIP  = 0,
    parameter X_SAMPLE_STEP = 2,
    parameter Y_SAMPLE_STEP = 2,
    parameter SEG_PIXELS    = 960,
    parameter INTER_PKT_GAP = 1024
)(
    input  wire        capture_req_toggle,

    input  wire        pix_clk,
    input  wire        pix_rst_n,
    input  wire        pix_vs,
    input  wire        pix_de,
    input  wire [7:0]  pix_data,

    input  wire        eth_clk,
    input  wire        eth_rst_n,
    input  wire        udp_tx_done,
    input  wire        udp_tx_req,
    output reg         udp_tx_start_en,
    output reg  [7:0]  udp_tx_data,
    output wire [15:0] udp_tx_byte_num,
    output wire        capture_busy
);

    localparam [15:0] HEADER_LEN     = 16'd14;
    localparam [15:0] OUT_WIDTH_U16  = OUT_WIDTH;
    localparam [15:0] OUT_HEIGHT_U16 = OUT_HEIGHT;
    localparam [15:0] SEG_PIXELS_U16 = SEG_PIXELS;
    localparam [15:0] SEG_COUNT_U16  = (OUT_WIDTH + SEG_PIXELS - 1) / SEG_PIXELS;
    localparam [15:0] PACKET_LEN     = SEG_PIXELS_U16 + HEADER_LEN;
    localparam [15:0] X_SKIP_U16     = X_START_SKIP;
    localparam [15:0] Y_SKIP_U16     = Y_START_SKIP;
    localparam [15:0] X_STEP_U16     = X_SAMPLE_STEP;
    localparam [15:0] Y_STEP_U16     = Y_SAMPLE_STEP;
    localparam [15:0] X_CAPTURE_END  = X_SKIP_U16 + OUT_WIDTH_U16 * X_STEP_U16;
    localparam [15:0] Y_CAPTURE_END  = Y_SKIP_U16 + OUT_HEIGHT_U16 * Y_STEP_U16;

    reg [7:0] line_buf0 [0:OUT_WIDTH-1];
    reg [7:0] line_buf1 [0:OUT_WIDTH-1];

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
    reg [15:0] active_payload_len;
    reg [15:0] segment_id;
    reg        tx_req_d;
    reg        tx_req_skip;
    reg [15:0] rd_addr;
    wire [15:0] segment_x_offset;
    wire [15:0] segment_remain_len;
    wire [15:0] payload_rd_offset;
    wire [15:0] line_rd_addr;
    wire        payload_rd_valid;
    wire        send_active_eth;

    assign udp_tx_byte_num = PACKET_LEN;
    assign pix_vs_rise = pix_vs && !pix_vs_d;
    assign pix_de_rise = pix_de && !pix_de_d;
    assign pix_de_fall = !pix_de && pix_de_d;
    assign in_y_window = (src_y >= Y_SKIP_U16) && (src_y < Y_CAPTURE_END);
    assign segment_x_offset = segment_id * SEG_PIXELS_U16;
    assign segment_remain_len = OUT_WIDTH_U16 - segment_x_offset;
    assign payload_rd_offset = rd_addr - HEADER_LEN;
    assign line_rd_addr = active_x_offset + payload_rd_offset;
    assign payload_rd_valid = payload_rd_offset < active_payload_len;
    assign send_active_eth = pending_valid || tx_busy || (gap_cnt != 16'd0);
    assign send_active_pix = send_active_sync[2];
    assign capture_busy = capture_active || capture_pending || send_active_pix;

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
                       (sample_x < OUT_WIDTH_U16)) begin
                        if(x_step_cnt == 16'd0) begin
                            if(wr_buf_sel)
                                line_buf1[sample_x] <= pix_data;
                            else
                                line_buf0[sample_x] <= pix_data;
                            sample_x <= sample_x + 1'b1;
                        end

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

                        if(sample_y >= OUT_HEIGHT_U16 - 1'b1) begin
                            capture_active <= 1'b0;
                        end
                        else begin
                            sample_y <= sample_y + 1'b1;
                        end
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
                if(segment_remain_len > SEG_PIXELS_U16)
                    active_payload_len <= SEG_PIXELS_U16;
                else
                    active_payload_len <= segment_remain_len;
                tx_busy         <= 1'b1;
                udp_tx_start_en <= 1'b1;
            end
        end
    end

    always @(posedge eth_clk or negedge eth_rst_n) begin
        if(!eth_rst_n) begin
            tx_req_d    <= 1'b0;
            tx_req_skip <= 1'b0;
            rd_addr     <= 16'd0;
        end
        else begin
            tx_req_d <= udp_tx_req;

            if(udp_tx_start_en) begin
                tx_req_skip <= 1'b0;
                rd_addr     <= 16'd0;
            end
            else if(udp_tx_req && !tx_req_d) begin
                tx_req_skip <= 1'b1;
                rd_addr     <= 16'd0;
            end
            else if(udp_tx_req) begin
                if(tx_req_skip) begin
                    tx_req_skip <= 1'b0;
                    rd_addr     <= 16'd1;
                end
                else if(rd_addr < PACKET_LEN - 1'b1) begin
                    rd_addr <= rd_addr + 1'b1;
                end
            end
        end
    end

    always @(*) begin
        case(rd_addr)
            16'd0: udp_tx_data = 8'h49;
            16'd1: udp_tx_data = 8'h4d;
            16'd2: udp_tx_data = active_frame_id;
            16'd3: udp_tx_data = 8'h02;
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
                else if(active_buf_sel)
                    udp_tx_data = line_buf1[line_rd_addr];
                else
                    udp_tx_data = line_buf0[line_rd_addr];
            end
        endcase
    end

endmodule

module udp_ddr_pkt #(
    parameter OUT_WIDTH       = 1920,
    parameter OUT_HEIGHT      = 1080,
    parameter SEG_PIXELS      = 1280,
    parameter INTER_PKT_GAP   = 128,
    parameter CTRL_ADDR_WIDTH = 28,
    parameter MEM_DQ_WIDTH    = 32,
    parameter LINE_ADDR_WIDTH = 22,
    parameter PIX_WIDTH       = 16
)(
    input  wire        capture_req_toggle,

    input  wire        ddr_clk,
    input  wire        ddr_rst_n,
    input  wire        ddr_frame_ready,
    input  wire        upload_frame_sel,
    output wire        upload_active,
    output reg         ddr_rd_cmd_en,
    output reg  [CTRL_ADDR_WIDTH-1:0] ddr_rd_cmd_addr,
    output wire [31:0] ddr_rd_cmd_len,
    input  wire        ddr_rd_cmd_ready,
    input  wire        ddr_rd_cmd_done,
    output wire        ddr_read_ready,
    input  wire [MEM_DQ_WIDTH*8-1:0] ddr_read_rdata,
    input  wire        ddr_read_en,

    input  wire        eth_clk,
    input  wire        eth_rst_n,
    input  wire        udp_tx_done,
    input  wire        udp_tx_req,
    output reg         udp_tx_start_en,
    output reg  [7:0]  udp_tx_data,
    output wire [15:0] udp_tx_byte_num,
    output wire        capture_busy
);

    localparam [15:0] HEADER_LEN       = 16'd14;
    localparam [15:0] OUT_WIDTH_U16    = OUT_WIDTH;
    localparam [15:0] OUT_HEIGHT_U16   = OUT_HEIGHT;
    localparam [15:0] SEG_PIXELS_U16   = SEG_PIXELS;
    localparam [15:0] SEG_COUNT_U16    = (OUT_WIDTH + SEG_PIXELS - 1) / SEG_PIXELS;
    localparam [15:0] PACKET_LEN       = SEG_PIXELS_U16 + HEADER_LEN;
    localparam [15:0] PIXELS_PER_BEAT  = (MEM_DQ_WIDTH * 8) / PIX_WIDTH;
    localparam [15:0] DDR_LINE_BEATS   = (OUT_WIDTH * PIX_WIDTH) / (MEM_DQ_WIDTH * 8);
    localparam [LINE_ADDR_WIDTH-1:0] LINE_ADDR_OFFSET =
        DDR_LINE_BEATS * (MEM_DQ_WIDTH * 8) / MEM_DQ_WIDTH;

    localparam [2:0] D_IDLE      = 3'd0;
    localparam [2:0] D_DRAIN     = 3'd1;
    localparam [2:0] D_CMD       = 3'd2;
    localparam [2:0] D_READ      = 3'd3;
    localparam [2:0] D_WAIT_SENT = 3'd4;
    localparam [7:0] DRAIN_CYCLES = 8'd64;

    reg [2:0]  ddr_state;
    reg [2:0]  capture_req_sync;
    reg        capture_req_seen;
    reg [2:0]  line_sent_sync;
    reg        line_sent_seen;
    reg [7:0]  frame_id_ddr;
    reg [7:0]  active_frame_id_ddr;
    reg [15:0] row_id_ddr;
    reg [LINE_ADDR_WIDTH-1:0] row_addr_ddr;
    reg        active_frame_sel_ddr;
    reg [15:0] beat_cnt;
    reg [7:0]  drain_cnt;
    reg        line_ready_toggle_ddr;
    reg [15:0] line_ready_row_ddr;
    reg [7:0]  line_ready_frame_ddr;

    reg [2:0]  line_ready_sync;
    reg        line_ready_seen_eth;
    reg [2:0]  upload_active_sync_eth;
    reg        row_active;
    reg        tx_busy;
    reg [15:0] gap_cnt;
    reg [15:0] pending_row_id;
    reg [7:0]  pending_frame_id;
    reg [15:0] active_row_id;
    reg [7:0]  active_frame_id;
    reg [15:0] active_x_offset;
    reg [15:0] active_payload_len;
    reg [15:0] segment_id;
    reg        line_sent_toggle_eth;
    reg        tx_req_d;
    reg        tx_req_skip;
    reg [15:0] rd_addr;
    reg        line_ram_lsb_d1;
    wire [31:0] line_ram_rdata;

    wire [CTRL_ADDR_WIDTH-1:0] current_rd_addr;
    wire [15:0] segment_x_offset;
    wire [15:0] segment_remain_len;
    wire [15:0] payload_rd_offset;
    wire [15:0] payload_prefetch_offset;
    wire [15:0] line_rd_addr;
    wire [15:0] line_prefetch_addr;
    wire [11:0] line_ram_rd_addr;
    wire [7:0]  line_ram_pix_data;
    wire        payload_rd_valid;

    assign upload_active = (ddr_state != D_IDLE);
    assign ddr_rd_cmd_len = {16'd0, DDR_LINE_BEATS};
    assign ddr_read_ready = 1'b1;
    assign udp_tx_byte_num = PACKET_LEN;
    assign capture_busy = upload_active_sync_eth[2] || row_active || tx_busy || (gap_cnt != 16'd0);

    assign current_rd_addr = {{(CTRL_ADDR_WIDTH-LINE_ADDR_WIDTH-1){1'b0}},
                              active_frame_sel_ddr,
                              row_addr_ddr};

    assign segment_x_offset = segment_id * SEG_PIXELS_U16;
    assign segment_remain_len = OUT_WIDTH_U16 - segment_x_offset;
    assign payload_rd_offset = rd_addr - HEADER_LEN;
    assign payload_prefetch_offset = (rd_addr >= HEADER_LEN - 16'd1) ?
                                     (rd_addr - HEADER_LEN + 16'd1) : 16'd0;
    assign line_rd_addr = active_x_offset + payload_rd_offset;
    assign line_prefetch_addr = active_x_offset + payload_prefetch_offset;
    assign line_ram_rd_addr = line_prefetch_addr[11:1];
    assign line_ram_pix_data = line_ram_lsb_d1 ? line_ram_rdata[23:16] : line_ram_rdata[7:0];
    assign payload_rd_valid = payload_rd_offset < active_payload_len;

    rd_fram_buf u_udp_line_buf (
        .wr_data    (ddr_read_rdata   ),
        .wr_addr    (beat_cnt[8:0]    ),
        .wr_en      (ddr_read_en      ),
        .wr_clk     (ddr_clk          ),
        .wr_rst     (~ddr_rst_n       ),
        .rd_addr    (line_ram_rd_addr ),
        .rd_data    (line_ram_rdata   ),
        .rd_clk     (eth_clk          ),
        .rd_rst     (~eth_rst_n       )
    );

    always @(posedge ddr_clk or negedge ddr_rst_n) begin
        if(!ddr_rst_n) begin
            ddr_state            <= D_IDLE;
            capture_req_sync     <= 3'd0;
            capture_req_seen     <= 1'b0;
            line_sent_sync       <= 3'd0;
            line_sent_seen       <= 1'b0;
            frame_id_ddr         <= 8'd0;
            active_frame_id_ddr  <= 8'd0;
            row_id_ddr           <= 16'd0;
            row_addr_ddr         <= {LINE_ADDR_WIDTH{1'b0}};
            active_frame_sel_ddr <= 1'b0;
            beat_cnt             <= 16'd0;
            drain_cnt            <= 8'd0;
            line_ready_toggle_ddr <= 1'b0;
            line_ready_row_ddr   <= 16'd0;
            line_ready_frame_ddr <= 8'd0;
            ddr_rd_cmd_en        <= 1'b0;
            ddr_rd_cmd_addr      <= {CTRL_ADDR_WIDTH{1'b0}};
        end
        else begin
            capture_req_sync <= {capture_req_sync[1:0], capture_req_toggle};
            line_sent_sync   <= {line_sent_sync[1:0], line_sent_toggle_eth};
            ddr_rd_cmd_en    <= 1'b0;

            case(ddr_state)
                D_IDLE: begin
                    if((capture_req_sync[2] != capture_req_seen) && ddr_frame_ready) begin
                        capture_req_seen     <= capture_req_sync[2];
                        frame_id_ddr         <= frame_id_ddr + 8'd1;
                        active_frame_id_ddr  <= frame_id_ddr + 8'd1;
                        active_frame_sel_ddr <= upload_frame_sel;
                        row_id_ddr           <= 16'd0;
                        row_addr_ddr         <= {LINE_ADDR_WIDTH{1'b0}};
                        beat_cnt             <= 16'd0;
                        drain_cnt            <= 8'd0;
                        ddr_state            <= D_DRAIN;
                    end
                end

                D_DRAIN: begin
                    if(ddr_read_en || !ddr_rd_cmd_ready) begin
                        drain_cnt <= 8'd0;
                    end
                    else if(drain_cnt >= DRAIN_CYCLES) begin
                        drain_cnt <= 8'd0;
                        ddr_state <= D_CMD;
                    end
                    else begin
                        drain_cnt <= drain_cnt + 8'd1;
                    end
                end

                D_CMD: begin
                    if(ddr_rd_cmd_ready) begin
                        ddr_rd_cmd_en   <= 1'b1;
                        ddr_rd_cmd_addr <= current_rd_addr;
                        beat_cnt        <= 16'd0;
                        ddr_state       <= D_READ;
                    end
                end

                D_READ: begin
                    if(ddr_read_en) begin
                        if(beat_cnt >= DDR_LINE_BEATS - 16'd1) begin
                            line_ready_row_ddr    <= row_id_ddr;
                            line_ready_frame_ddr  <= active_frame_id_ddr;
                            line_ready_toggle_ddr <= ~line_ready_toggle_ddr;
                            ddr_state             <= D_WAIT_SENT;
                        end
                        else begin
                            beat_cnt <= beat_cnt + 16'd1;
                        end
                    end
                end

                D_WAIT_SENT: begin
                    if(line_sent_sync[2] != line_sent_seen) begin
                        line_sent_seen <= line_sent_sync[2];
                        if(row_id_ddr >= OUT_HEIGHT_U16 - 16'd1) begin
                            ddr_state <= D_IDLE;
                        end
                        else begin
                            row_id_ddr   <= row_id_ddr + 16'd1;
                            row_addr_ddr <= row_addr_ddr + LINE_ADDR_OFFSET;
                            ddr_state    <= D_CMD;
                        end
                    end
                end

                default: begin
                    ddr_state <= D_IDLE;
                end
            endcase
        end
    end

    always @(posedge eth_clk or negedge eth_rst_n) begin
        if(!eth_rst_n) begin
            line_ready_sync      <= 3'd0;
            line_ready_seen_eth  <= 1'b0;
            upload_active_sync_eth <= 3'd0;
            row_active           <= 1'b0;
            tx_busy              <= 1'b0;
            gap_cnt              <= 16'd0;
            pending_row_id       <= 16'd0;
            pending_frame_id     <= 8'd0;
            active_row_id        <= 16'd0;
            active_frame_id      <= 8'd0;
            active_x_offset      <= 16'd0;
            active_payload_len   <= 16'd0;
            segment_id           <= 16'd0;
            line_sent_toggle_eth <= 1'b0;
            udp_tx_start_en      <= 1'b0;
        end
        else begin
            line_ready_sync <= {line_ready_sync[1:0], line_ready_toggle_ddr};
            upload_active_sync_eth <= {upload_active_sync_eth[1:0], upload_active};
            udp_tx_start_en <= 1'b0;

            if(line_ready_sync[2] != line_ready_seen_eth) begin
                line_ready_seen_eth <= line_ready_sync[2];
                row_active         <= 1'b1;
                pending_row_id     <= line_ready_row_ddr;
                pending_frame_id   <= line_ready_frame_ddr;
                segment_id         <= 16'd0;
            end

            if(udp_tx_done) begin
                tx_busy <= 1'b0;
                gap_cnt <= INTER_PKT_GAP[15:0];

                if(segment_id >= SEG_COUNT_U16 - 16'd1) begin
                    row_active           <= 1'b0;
                    segment_id           <= 16'd0;
                    line_sent_toggle_eth <= ~line_sent_toggle_eth;
                end
                else begin
                    segment_id <= segment_id + 16'd1;
                end
            end
            else if(gap_cnt != 16'd0) begin
                gap_cnt <= gap_cnt - 16'd1;
            end

            if(row_active && !tx_busy && (gap_cnt == 16'd0)) begin
                active_row_id   <= pending_row_id;
                active_frame_id <= pending_frame_id;
                active_x_offset <= segment_x_offset;
                if(segment_remain_len > SEG_PIXELS_U16)
                    active_payload_len <= SEG_PIXELS_U16;
                else
                    active_payload_len <= segment_remain_len;
                tx_busy         <= 1'b1;
                udp_tx_start_en <= 1'b1;
            end
        end
    end

    always @(posedge eth_clk or negedge eth_rst_n) begin
        if(!eth_rst_n) begin
            tx_req_d    <= 1'b0;
            tx_req_skip <= 1'b0;
            rd_addr     <= 16'd0;
            line_ram_lsb_d1 <= 1'b0;
        end
        else begin
            tx_req_d <= udp_tx_req;
            line_ram_lsb_d1 <= line_prefetch_addr[0];

            if(udp_tx_start_en) begin
                tx_req_skip <= 1'b0;
                rd_addr     <= 16'd0;
            end
            else if(udp_tx_req && !tx_req_d) begin
                tx_req_skip <= 1'b1;
                rd_addr     <= 16'd0;
            end
            else if(udp_tx_req) begin
                if(tx_req_skip) begin
                    tx_req_skip <= 1'b0;
                    rd_addr     <= 16'd1;
                end
                else if(rd_addr < PACKET_LEN - 16'd1) begin
                    rd_addr <= rd_addr + 16'd1;
                end
            end
        end
    end

    always @(*) begin
        case(rd_addr)
            16'd0: udp_tx_data = 8'h49;
            16'd1: udp_tx_data = 8'h4d;
            16'd2: udp_tx_data = active_frame_id;
            16'd3: udp_tx_data = 8'h02;
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
                else
                    udp_tx_data = line_ram_pix_data;
            end
        endcase
    end

endmodule
