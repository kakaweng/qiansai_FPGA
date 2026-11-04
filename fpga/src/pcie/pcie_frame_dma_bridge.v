`timescale 1ns / 1ps

module pcie_frame_dma_bridge #(
    parameter CTRL_ADDR_WIDTH = 28,
    parameter RGB565 = 0,
    parameter ENABLE_CAMERA = 0,
    parameter ENABLE_ETHERNET = 0,
    parameter CAMERA_FRAME_BASE = 28'h0800000
)(
    input                           pcie_clk,
    input                           pcie_rst_n,
    input                           bar1_wr_en,
    input      [11:0]               bar1_wr_addr,
    input      [127:0]              bar1_wr_data,
    input      [15:0]               bar1_wr_byte_en,

    output reg                      ext_dma_mode,
    output reg                      ext_mwr64_req,
    input                           ext_mwr64_req_ack,
    input                           ext_mwr_busy,
    output reg [9:0]                ext_req_length,
    output reg [63:0]               ext_req_addr,
    input                           ext_rd_clk_en,
    input      [11:0]               ext_rd_addr,
    output     [127:0]              ext_rd_data,
    output     [127:0]              status_data,

    input                           ddr_clk,
    input                           ddr_rst_n,
    output reg                      upload_active,
    output reg [1:0]                upload_source,
    output reg                      upload_rd_cmd_en,
    output reg [CTRL_ADDR_WIDTH-1:0] upload_rd_cmd_addr,
    output reg [31:0]               upload_rd_cmd_len,
    input                           upload_rd_cmd_ready,
    input                           upload_rd_cmd_done,
    output                          upload_read_ready,
    input      [255:0]              upload_read_rdata,
    input                           upload_read_en,
    input                           upload_frame_sel,
    input                           upload_frame_ready,
    input                           camera_frame_ready,
    input                           ethernet_frame_ready,
    input      [CTRL_ADDR_WIDTH-1:0] ethernet_frame_base,
    input                           ddr_axi_arvalid,
    input                           ddr_axi_arready
);

    localparam [2:0] P_IDLE       = 3'd0;
    localparam [2:0] P_WAIT_LOAD  = 3'd1;
    localparam [2:0] P_SEND_DMA   = 3'd2;
    localparam [2:0] P_WAIT_DMA   = 3'd3;
    localparam [10:0] HDMI_LAST_CHUNK = RGB565 ? 11'd2024 : 11'd1012;
    wire [10:0] last_chunk = active_source != 0 ? 11'd299 : HDMI_LAST_CHUNK;

    localparam [1:0] D_IDLE       = 2'd0;
    localparam [1:0] D_WAIT_READY = 2'd1;
    localparam [1:0] D_WAIT_DONE  = 2'd2;
    localparam [1:0] D_WAIT_FRAME = 2'd3;

    reg [2:0]  pcie_state;
    reg [10:0] chunk_index;
    reg [63:0] host_dma_addr;
    reg        load_request_toggle;
    reg        load_done_meta;
    reg        load_done_sync;
    reg        load_done_seen;
    reg        dma_busy_seen;
    reg        frame_done;
    reg        frame_error;
    reg [1:0]  selected_source;
    reg [1:0]  active_source;

    reg [1:0]  ddr_state;
    reg        request_meta;
    reg        request_sync;
    reg        request_seen;
    reg [10:0] request_chunk;
    reg [1:0]  request_source;
    reg [CTRL_ADDR_WIDTH-1:0] ethernet_base_latched;
    reg [10:0] ddr_chunk;
    reg        frame_select_latched;
    reg        load_done_toggle;
    reg [11:0] stage_wr_addr;
    wire [127:0] stage_wr_data;
    reg [7:0]  upload_beat_count;
    reg        upload_command_seen;
    reg        upload_done_seen;
    (* ASYNC_REG = "TRUE" *) reg [16:0] ddr_debug_meta;
    (* ASYNC_REG = "TRUE" *) reg [16:0] ddr_debug_sync;
    (* ASYNC_REG = "TRUE" *) reg [1:0] ddr_ready_sync;
    wire [31:0] debug_data;

    wire write_low_addr;
    wire write_high_addr;
    wire write_start;
    wire write_source;

    assign write_low_addr  = bar1_wr_en && (|bar1_wr_byte_en[3:0]) &&
                             (bar1_wr_addr[7:0] == 8'h13);
    assign write_high_addr = bar1_wr_en && (|bar1_wr_byte_en[3:0]) &&
                             (bar1_wr_addr[7:0] == 8'h14);
    assign write_start     = bar1_wr_en && (|bar1_wr_byte_en[3:0]) &&
                             (bar1_wr_addr[7:0] == 8'h15) && bar1_wr_data[0];
    assign write_source    = bar1_wr_en && bar1_wr_byte_en[0] &&
                             (bar1_wr_addr[7:0] == 8'h16);

    assign debug_data = {8'hd4, ddr_debug_sync[16:9], ddr_ready_sync[1],
                         ddr_debug_sync[8:0], pcie_state, ext_mwr_busy,
                         ext_mwr64_req, 1'b0};
    assign status_data = {
        debug_data,
        RGB565 ? 32'h52473536 : 32'h46524d45,
        RGB565 ? 32'd4147200 : 32'd2073600,
        5'd0, chunk_index, 13'd0, frame_error, frame_done, ext_dma_mode
    };

    always @(posedge pcie_clk or negedge pcie_rst_n) begin
        if (!pcie_rst_n) begin
            host_dma_addr       <= 64'd0;
            pcie_state          <= P_IDLE;
            chunk_index         <= 11'd0;
            load_request_toggle <= 1'b0;
            load_done_meta      <= 1'b0;
            load_done_sync      <= 1'b0;
            load_done_seen      <= 1'b0;
            dma_busy_seen       <= 1'b0;
            ext_dma_mode        <= 1'b0;
            ext_mwr64_req       <= 1'b0;
            ext_req_length      <= 10'd0;
            ext_req_addr        <= 64'd0;
            frame_done          <= 1'b0;
            frame_error         <= 1'b0;
            selected_source     <= 1'b0;
            active_source       <= 1'b0;
            ddr_debug_meta      <= 17'd0;
            ddr_debug_sync      <= 17'd0;
            ddr_ready_sync      <= 2'd0;
        end else begin
            load_done_meta <= load_done_toggle;
            load_done_sync <= load_done_meta;
            ddr_debug_meta <= {upload_beat_count, upload_frame_ready,
                               ddr_axi_arvalid, ddr_axi_arready,
                               upload_command_seen, upload_done_seen,
                               upload_rd_cmd_ready, upload_active, ddr_state};
            ddr_debug_sync <= ddr_debug_meta;
            ddr_ready_sync <= {ddr_ready_sync[0], ddr_rst_n};

            if (write_low_addr)
                host_dma_addr[31:0] <= bar1_wr_data[31:0];
            if (write_high_addr)
                host_dma_addr[63:32] <= bar1_wr_data[31:0];
            if (write_source && pcie_state == P_IDLE) begin
                if (ENABLE_ETHERNET && RGB565 && bar1_wr_data[1:0] == 2)
                    selected_source <= 2;
                else selected_source <= ENABLE_CAMERA && RGB565 && bar1_wr_data[1:0] == 1 ? 1 : 0;
            end

            case (pcie_state)
                P_IDLE: begin
                    ext_mwr64_req <= 1'b0;
                    if (write_start) begin
                        active_source       <= selected_source;
                        chunk_index         <= 11'd0;
                        frame_done          <= 1'b0;
                        frame_error         <= 1'b0;
                        ext_dma_mode        <= 1'b1;
                        load_request_toggle <= ~load_request_toggle;
                        pcie_state          <= P_WAIT_LOAD;
                    end
                end

                P_WAIT_LOAD: begin
                    if (load_done_sync != load_done_seen) begin
                        load_done_seen <= load_done_sync;
                        ext_req_addr   <= host_dma_addr + {42'd0, chunk_index, 11'd0};
                        ext_req_length <= (!RGB565 && chunk_index == last_chunk) ? 10'd256 : 10'd512;
                        ext_mwr64_req  <= 1'b1;
                        dma_busy_seen  <= 1'b0;
                        pcie_state     <= P_SEND_DMA;
                    end
                end

                P_SEND_DMA: begin
                    if (ext_mwr64_req_ack) begin
                        ext_mwr64_req <= 1'b0;
                        pcie_state    <= P_WAIT_DMA;
                    end
                end

                P_WAIT_DMA: begin
                    if (ext_mwr_busy)
                        dma_busy_seen <= 1'b1;
                    if (dma_busy_seen && !ext_mwr_busy) begin
                        if (chunk_index == last_chunk) begin
                            ext_dma_mode <= 1'b0;
                            frame_done   <= 1'b1;
                            pcie_state   <= P_IDLE;
                        end else begin
                            chunk_index         <= chunk_index + 11'd1;
                            load_request_toggle <= ~load_request_toggle;
                            pcie_state          <= P_WAIT_LOAD;
                        end
                    end
                end

                default: pcie_state <= P_IDLE;
            endcase
        end
    end

    always @(posedge ddr_clk or negedge ddr_rst_n) begin
        if (!ddr_rst_n) begin
            request_meta          <= 1'b0;
            request_sync          <= 1'b0;
            request_seen          <= 1'b0;
            request_chunk         <= 11'd0;
            request_source        <= 1'b0;
            ddr_chunk             <= 11'd0;
            ddr_state             <= D_IDLE;
            frame_select_latched  <= 1'b0;
            ethernet_base_latched <= 0;
            load_done_toggle      <= 1'b0;
            upload_active         <= 1'b0;
            upload_source         <= 1'b0;
            upload_rd_cmd_en      <= 1'b0;
            upload_rd_cmd_addr    <= {CTRL_ADDR_WIDTH{1'b0}};
            upload_rd_cmd_len     <= 32'd0;
            stage_wr_addr         <= 12'd0;
            upload_beat_count     <= 8'd0;
            upload_command_seen   <= 1'b0;
            upload_done_seen      <= 1'b0;
        end else begin
            request_meta  <= load_request_toggle;
            request_sync  <= request_meta;
            request_chunk <= chunk_index;
            request_source <= active_source;
            upload_rd_cmd_en <= 1'b0;

            if (upload_read_en && ddr_state == D_WAIT_DONE) begin
                stage_wr_addr <= stage_wr_addr + 12'd1;
                upload_beat_count <= upload_beat_count + 8'd1;
            end
            if (upload_rd_cmd_en)
                upload_command_seen <= 1'b1;
            if (upload_rd_cmd_done && ddr_state == D_WAIT_DONE)
                upload_done_seen <= 1'b1;

            case (ddr_state)
                D_IDLE: begin
                    if (request_sync != request_seen) begin
                        request_seen <= request_sync;
                        ddr_chunk    <= request_chunk;
                        stage_wr_addr <= 12'd0;
                        upload_beat_count <= 8'd0;
                        upload_command_seen <= 1'b0;
                        upload_done_seen <= 1'b0;
                        upload_rd_cmd_addr <=
                            (request_source == 2 ? ethernet_base_latched : request_source == 1 ? CAMERA_FRAME_BASE : {5'd0,
                             (request_chunk == 11'd0) ? upload_frame_sel : frame_select_latched,
                             22'd0}) + ({17'd0, request_chunk} << (RGB565 ? 9 : 10));
                        upload_rd_cmd_len <=
                            (RGB565 || request_chunk == HDMI_LAST_CHUNK) ? 32'd64 : 32'd128;
                        if (request_chunk == 11'd0) begin
                            upload_active <= 1'b1;
                            upload_source <= request_source;
                        end
                        ddr_state <= (request_chunk == 11'd0) ? D_WAIT_FRAME : D_WAIT_READY;
                    end
                end

                D_WAIT_FRAME: begin
                    if (upload_source == 2 ? ethernet_frame_ready :
                        upload_source == 1 ? camera_frame_ready : upload_frame_ready) begin
                        frame_select_latched <= upload_frame_sel;
                        ethernet_base_latched <= ethernet_frame_base;
                        upload_rd_cmd_addr <= upload_source == 2 ? ethernet_frame_base :
                                              upload_source == 1 ? CAMERA_FRAME_BASE :
                                              {5'd0, upload_frame_sel, 22'd0};
                        upload_active <= 1'b1;
                        ddr_state <= D_WAIT_READY;
                    end
                end

                D_WAIT_READY: begin
                    if (upload_rd_cmd_ready) begin
                        upload_rd_cmd_en <= 1'b1;
                        ddr_state <= D_WAIT_DONE;
                    end
                end

                D_WAIT_DONE: begin
                    if (upload_rd_cmd_done) begin
                        if (ddr_chunk == (upload_source ? 11'd299 : HDMI_LAST_CHUNK))
                            upload_active <= 1'b0;
                        load_done_toggle <= ~load_done_toggle;
                        ddr_state <= D_IDLE;
                    end
                end

                default: ddr_state <= D_IDLE;
            endcase
        end
    end

    function [127:0] gray_pack;
        input [255:0] raw_pixels;
        integer pixel_index;
        begin
            for (pixel_index = 0; pixel_index < 16; pixel_index = pixel_index + 1)
                gray_pack[pixel_index*8 +: 8] = raw_pixels[pixel_index*16 +: 8];
        end
    endfunction

    assign stage_wr_data = gray_pack(upload_read_rdata);
    assign upload_read_ready = 1'b1;

    generate
    if (RGB565) begin : g_rgb565
        wire [127:0] low_data;
        wire [127:0] high_data;
        reg read_high;

        always @(posedge pcie_clk or negedge pcie_rst_n) begin
            if (!pcie_rst_n)
                read_high <= 1'b0;
            else if (ext_rd_clk_en)
                read_high <= ext_rd_addr[0];
        end

        assign ext_rd_data = read_high ? high_data : low_data;

        ipsl_pcie_dma_ram u_stage_low (
            .wr_data (upload_read_rdata[127:0]), .wr_addr (stage_wr_addr),
            .wr_en (upload_read_en && ddr_state == D_WAIT_DONE),
            .wr_clk (ddr_clk), .wr_rst (~ddr_rst_n), .wr_byte_en (16'hffff),
            .rd_data (low_data), .rd_addr ({1'b0, ext_rd_addr[11:1]}),
            .rd_clk (pcie_clk), .rd_clk_en (ext_rd_clk_en), .rd_rst (~pcie_rst_n)
        );
        ipsl_pcie_dma_ram u_stage_high (
            .wr_data (upload_read_rdata[255:128]), .wr_addr (stage_wr_addr),
            .wr_en (upload_read_en && ddr_state == D_WAIT_DONE),
            .wr_clk (ddr_clk), .wr_rst (~ddr_rst_n), .wr_byte_en (16'hffff),
            .rd_data (high_data), .rd_addr ({1'b0, ext_rd_addr[11:1]}),
            .rd_clk (pcie_clk), .rd_clk_en (ext_rd_clk_en), .rd_rst (~pcie_rst_n)
        );
    end else begin : g_gray
    ipsl_pcie_dma_ram u_frame_stage_ram (
        .wr_data      (stage_wr_data),
        .wr_addr      (stage_wr_addr),
        .wr_en        (upload_read_en && ddr_state == D_WAIT_DONE),
        .wr_clk       (ddr_clk),
        .wr_rst       (~ddr_rst_n),
        .wr_byte_en   (16'hffff),
        .rd_data      (ext_rd_data),
        .rd_addr      (ext_rd_addr),
        .rd_clk       (pcie_clk),
        .rd_clk_en    (ext_rd_clk_en),
        .rd_rst       (~pcie_rst_n)
    );
    end
    endgenerate

endmodule
