`timescale 1ns/1ps

module pcie_hdmi_ddr_tb #(
    parameter RGB565 = 0,
    parameter CAMERA = 0,
    parameter ETHERNET = 0
);
    reg pcie_clk = 1'b0;
    reg ddr_clk = 1'b0;
    reg rst_n = 1'b0;
    reg bar_write = 1'b0;
    reg [11:0] bar_address = 12'd0;
    reg [127:0] bar_data = 128'd0;
    reg display_command = 1'b0;
    reg frame_ready = 1'b0;
    reg [1:0] expected_camera = ETHERNET ? 2 : CAMERA;
    wire [1:0] upload_source;
    wire upload_active;
    wire upload_command;
    wire [27:0] upload_address;
    wire [31:0] upload_length;
    wire upload_ready;
    wire upload_done;
    wire upload_data_valid;
    wire [255:0] upload_data;
    wire dma_request;
    reg dma_ack = 1'b0;
    reg dma_busy = 1'b0;
    wire [63:0] dma_address;
    wire [9:0] dma_length;
    wire [127:0] status_data;
    reg ram_read_enable = 1'b0;
    reg [11:0] ram_read_address = 12'd0;
    wire [127:0] ram_read_data;
    wire [27:0] axi_address;
    wire [3:0] axi_length;
    wire axi_address_valid;
    reg axi_address_ready = 1'b0;
    reg [255:0] axi_data = 256'd0;
    reg axi_data_valid = 1'b0;
    reg axi_last = 1'b0;
    integer upload_beats = 0;
    integer upload_commands = 0;
    integer dma_chunks = 0;
    integer pending_bursts = 0;
    reg [27:0] address_queue [0:15];
    reg [3:0] length_queue [0:15];
    integer queue_write = 0;
    integer queue_read = 0;
    integer response_beat = 0;
    integer response_delay = 0;
    integer response_length = 0;
    reg [27:0] response_address = 28'd0;
    reg response_active = 1'b0;
    integer pixel_index;
    integer cycles;
    integer word_index;
    localparam [63:0] HOST_ADDRESS = 64'h123456787a100000;

    always #4 pcie_clk = ~pcie_clk;
    always #5 ddr_clk = ~ddr_clk;

    pcie_frame_dma_bridge #(.RGB565(RGB565), .ENABLE_CAMERA(CAMERA), .ENABLE_ETHERNET(ETHERNET)) bridge (
        .pcie_clk (pcie_clk), .pcie_rst_n (rst_n),
        .bar1_wr_en (bar_write), .bar1_wr_addr (bar_address),
        .bar1_wr_data (bar_data), .bar1_wr_byte_en (16'h000f),
        .ext_dma_mode (), .ext_mwr64_req (dma_request),
        .ext_mwr64_req_ack (dma_ack), .ext_mwr_busy (dma_busy),
        .ext_req_length (dma_length), .ext_req_addr (dma_address),
        .ext_rd_clk_en (ram_read_enable), .ext_rd_addr (ram_read_address),
        .ext_rd_data (ram_read_data), .status_data (status_data),
        .ddr_clk (ddr_clk), .ddr_rst_n (rst_n),
        .upload_active (upload_active), .upload_rd_cmd_en (upload_command),
        .upload_source (upload_source), .camera_frame_ready (frame_ready),
        .ethernet_frame_ready(frame_ready), .ethernet_frame_base(28'h0980000),
        .upload_rd_cmd_addr (upload_address), .upload_rd_cmd_len (upload_length),
        .upload_rd_cmd_ready (upload_ready), .upload_rd_cmd_done (upload_done),
        .upload_read_ready (), .upload_read_rdata (upload_data),
        .upload_read_en (upload_data_valid), .upload_frame_sel (1'b1),
        .upload_frame_ready (frame_ready), .ddr_axi_arvalid (axi_address_valid),
        .ddr_axi_arready (axi_address_ready)
    );

    fram_buf frame_buffer (
        .vin_clk (ddr_clk), .wr_fsync (1'b0), .wr_en (1'b0),
        .wr_data (16'd0), .wr_pause (1'b0), .init_done (),
        .ddr_clk (ddr_clk), .ddr_rstn (rst_n),
        .vout_clk (ddr_clk), .rd_fsync (1'b0), .rd_en (1'b0),
        .vout_de (), .vout_data (),
        .upload_active (upload_active), .upload_rd_cmd_en (upload_command),
        .upload_rd_cmd_addr (upload_address), .upload_rd_cmd_len (upload_length),
        .upload_rd_cmd_ready (upload_ready), .upload_rd_cmd_done (upload_done),
        .upload_read_ready (1'b1), .upload_read_rdata (upload_data),
        .upload_read_en (upload_data_valid), .upload_frame_sel (),
        .axi_awaddr (), .axi_awid (), .axi_awlen (), .axi_awsize (),
        .axi_awburst (), .axi_awready (1'b0), .axi_awvalid (),
        .axi_wdata (), .axi_wstrb (), .axi_wlast (1'b0),
        .axi_wvalid (), .axi_wready (1'b0), .axi_bid (4'd0),
        .axi_araddr (axi_address), .axi_arid (), .axi_arlen (axi_length),
        .axi_arsize (), .axi_arburst (), .axi_arvalid (axi_address_valid),
        .axi_arready (axi_address_ready), .axi_rready (),
        .axi_rdata (axi_data), .axi_rvalid (axi_data_valid),
        .axi_rlast (axi_last), .axi_rid (4'd0)
    );

    always @(posedge ddr_clk) begin
        if (rst_n && axi_address_valid && axi_address_ready) begin
            if (pending_bursts == 16)
                $fatal(1, "DDR response queue overflow");
            address_queue[queue_write] = axi_address;
            length_queue[queue_write] = axi_length;
            queue_write = (queue_write + 1) % 16;
            pending_bursts = pending_bursts + 1;
        end
        if (rst_n && upload_command) begin
            upload_commands = upload_commands + 1;
            upload_beats = 0;
        end
        if (rst_n && upload_data_valid) begin
            if (upload_commands == 0)
                $fatal(1, "Display data was routed into the upload before its command");
            upload_beats = upload_beats + 1;
        end
        if (rst_n && upload_done && upload_beats != upload_length)
            $fatal(1, "DDR completed before the last data beat: got=%0d expected=%0d",
                   upload_beats, upload_length);
    end

    always @(negedge ddr_clk) begin
        axi_address_ready = rst_n && ($time % 70 != 0);
        axi_data_valid = 1'b0;
        axi_last = 1'b0;
        if (response_active) begin
            if (response_delay != 0)
                response_delay = response_delay - 1;
            else begin
                for (pixel_index = 0; pixel_index < 16; pixel_index = pixel_index + 1)
                    axi_data[pixel_index*16 +: 16] =
                        (response_address*2 + response_beat*16 + pixel_index) & 16'hffff;
                axi_data_valid = 1'b1;
                axi_last = response_beat == response_length;
                if (axi_last)
                    response_active = 1'b0;
                response_beat = response_beat + 1;
                response_delay = 2;
                if (response_beat == response_length)
                    response_delay = 12;
            end
        end else if (pending_bursts != 0) begin
            response_address = address_queue[queue_read];
            response_length = length_queue[queue_read];
            queue_read = (queue_read + 1) % 16;
            pending_bursts = pending_bursts - 1;
            response_beat = 0;
            response_delay = 35;
            response_active = 1'b1;
        end
    end

    task write_register;
        input [11:0] address;
        input [31:0] value;
        begin
            @(negedge pcie_clk);
            bar_address = address;
            bar_data = {96'd0, value};
            bar_write = 1'b1;
            @(negedge pcie_clk);
            bar_write = 1'b0;
        end
    endtask

    task receive_frame;
        begin
            for (dma_chunks = 0; dma_chunks < (expected_camera ? 300 : (RGB565 ? 2025 : 1013)); dma_chunks = dma_chunks + 1) begin
                cycles = 0;
                while (!dma_request && cycles < 20000) begin
                    @(negedge pcie_clk);
                    cycles = cycles + 1;
                end
                if (!dma_request || dma_address !== HOST_ADDRESS + dma_chunks*2048 ||
                    dma_length !== ((!RGB565 && dma_chunks == 1012) ? 10'd256 : 10'd512))
                    $fatal(1, "DMA chunk %0d failed: status=%h debug=%h",
                           dma_chunks, status_data[31:0], status_data[127:96]);
                if (upload_beats != ((RGB565 || dma_chunks == 1012) ? 64 : 128))
                    $fatal(1, "Wrong DDR data count for chunk %0d", dma_chunks);
                if (upload_source !== expected_camera ||
                    upload_address !== (expected_camera == 2 ? 28'h0980000 : (expected_camera ? 28'h0800000 : 28'h0400000)) +
                                       dma_chunks*(RGB565 ? 512 : 1024))
                    $fatal(1, "Wrong DDR address for chunk %0d: %h", dma_chunks, upload_address);
                dma_ack = 1'b1;
                dma_busy = 1'b1;
                @(negedge pcie_clk);
                dma_ack = 1'b0;
                for (word_index = 0; word_index < dma_length/4; word_index = word_index + 1) begin
                    ram_read_address = word_index;
                    ram_read_enable = 1'b1;
                    @(negedge pcie_clk);
                    if (RGB565) begin
                        for (pixel_index = 0; pixel_index < 8; pixel_index = pixel_index + 1)
                            if (ram_read_data[pixel_index*16 +: 16] !==
                                ((dma_chunks*1024 + word_index*8 + pixel_index) & 16'hffff))
                                $fatal(1, "Corrupt RGB565 RAM: chunk=%0d word=%0d pixel=%0d data=%h",
                                       dma_chunks, word_index, pixel_index, ram_read_data);
                    end else for (pixel_index = 0; pixel_index < 16; pixel_index = pixel_index + 1)
                        if (ram_read_data[pixel_index*8 +: 8] !==
                            ((word_index*16 + pixel_index) & 8'hff))
                            $fatal(1, "Corrupt staging RAM: chunk=%0d word=%0d byte=%0d data=%h",
                                   dma_chunks, word_index, pixel_index, ram_read_data);
                end
                ram_read_enable = 1'b0;
                repeat (3) @(negedge pcie_clk);
                dma_busy = 1'b0;
                repeat (3) @(negedge pcie_clk);
            end
            cycles = 0;
            while (status_data[2:0] !== 3'b010 && cycles < 30) begin
                @(negedge pcie_clk);
                cycles = cycles + 1;
            end
            if (status_data[2:0] !== 3'b010 || upload_active)
                $fatal(1, "Frame did not finish: status=%h", status_data[31:0]);
        end
    endtask

    initial begin
        force frame_buffer.disp_rd_cmd_en = display_command;
        force frame_buffer.disp_rd_cmd_addr = 28'h0000100;
        force frame_buffer.disp_rd_cmd_len = 32'd120;
        force frame_buffer.ddr_wreq = 1'b0;
        force frame_buffer.ddr_waddr = 28'd0;
        force frame_buffer.ddr_wr_len = 32'd120;
        force frame_buffer.ddr_wdata = 256'd0;
        repeat (8) @(negedge ddr_clk);
        rst_n = 1'b1;
        repeat (8) @(negedge ddr_clk);
        display_command = 1'b1;
        @(negedge ddr_clk);
        display_command = 1'b0;
        wait (axi_address_valid);
        write_register(12'h413, HOST_ADDRESS[31:0]);
        write_register(12'h414, HOST_ADDRESS[63:32]);
        if (CAMERA)
            write_register(12'h416, ETHERNET ? 32'd2 : 32'd1);
        write_register(12'h415, 32'd1);
        repeat (10) @(negedge ddr_clk);
        if (!upload_active || upload_command || dma_request)
            $fatal(1, "Capture did not wait for the first HDMI frame");
        if (status_data[127:120] !== 8'hd4 || status_data[111] !== 1'b1 ||
            status_data[110] !== 1'b0 || status_data[103:102] !== 2'd3 ||
            status_data[101:99] !== 3'd1)
            $fatal(1, "Waiting-for-frame diagnostics are incorrect: %h", status_data[127:96]);
        frame_ready = 1'b1;
        if (status_data[95:64] !== (RGB565 ? 32'h52473536 : 32'h46524d45) ||
            status_data[63:32] !== (RGB565 ? 32'd4147200 : 32'd2073600))
            $fatal(1, "Frame format metadata is incorrect");
        fork
            receive_frame;
            begin
                if (CAMERA) begin
                    wait (dma_request);
                    write_register(12'h416, 32'd0);
                end
            end
        join
        if (status_data[119:112] !== 8'd64)
            $fatal(1, "Final DDR beat count is incorrect in debug register");
        if (CAMERA) begin
            expected_camera = 0;
            write_register(12'h416, 32'd0);
        end
        write_register(12'h415, 32'd1);
        receive_frame;
        if (CAMERA) begin
            expected_camera = ETHERNET ? 2 : 1;
            write_register(12'h416, ETHERNET ? 32'd2 : 32'd1);
            write_register(12'h415, 32'd1);
            receive_frame;
        end
        $display("HDMI/camera/Ethernet DDR upload regression passed: RGB565=%0d CAMERA=%0d ETHERNET=%0d, full frames, source switching, display handover and delayed final beats", RGB565, CAMERA, ETHERNET);
        $finish;
    end

    initial begin
        #30000000;
        $fatal(1, "HDMI DDR upload simulation timed out");
    end
endmodule

module wr_fram_buf (
    input [31:0] wr_data,
    input [11:0] wr_addr,
    input wr_en, wr_clk, wr_rst,
    input [8:0] rd_addr,
    output [255:0] rd_data,
    input rd_clk, rd_rst
);
    assign rd_data = 256'd0;
endmodule

module rd_fram_buf (
    input [255:0] wr_data,
    input [8:0] wr_addr,
    input wr_en, wr_clk, wr_rst,
    input [11:0] rd_addr,
    output [31:0] rd_data,
    input rd_clk, rd_rst
);
    assign rd_data = 32'd0;
endmodule

module ipsl_pcie_dma_ram (
    input [127:0] wr_data,
    input [11:0] wr_addr,
    input wr_en, wr_clk, wr_rst,
    input [15:0] wr_byte_en,
    output reg [127:0] rd_data,
    input [11:0] rd_addr,
    input rd_clk, rd_clk_en, rd_rst
);
    reg [127:0] memory [0:4095];
    integer byte_index;
    always @(posedge wr_clk)
        if (!wr_rst && wr_en)
            for (byte_index = 0; byte_index < 16; byte_index = byte_index + 1)
                if (wr_byte_en[byte_index])
                    memory[wr_addr][byte_index*8 +: 8] <= wr_data[byte_index*8 +: 8];
    always @(posedge rd_clk)
        if (rd_rst)
            rd_data <= 128'd0;
        else if (rd_clk_en)
            rd_data <= memory[rd_addr];
endmodule
