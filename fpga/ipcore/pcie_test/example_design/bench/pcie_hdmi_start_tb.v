`timescale 1ns/1ps

module pcie_hdmi_start_tb;
    reg pcie_clk = 1'b0;
    reg ddr_clk = 1'b0;
    reg rst_n = 1'b0;
    reg write_start = 1'b0;
    reg [63:0] write_address = 64'd0;
    reg [127:0] write_data = 128'd0;
    wire bar1_write_enable;
    wire [11:0] bar1_write_address;
    wire [127:0] bar1_write_data;
    wire [15:0] bar1_byte_enable;
    wire [1:0] write_bar_hit;
    wire dma_mode;
    wire dma_request;
    wire [9:0] dma_length;
    wire [63:0] dma_address;
    wire [127:0] status_data;
    wire upload_active;
    wire upload_command;
    wire [27:0] upload_address;
    wire [31:0] upload_length;
    reg upload_done = 1'b0;
    localparam [63:0] HOST_DMA_ADDRESS = 64'h123456787a100000;

    always #4 pcie_clk = ~pcie_clk;
    always #5 ddr_clk = ~ddr_clk;

    ipsl_pcie_dma_wr_ctrl #(.ADDR_WIDTH (12)) write_controller (
        .clk (pcie_clk),
        .rst_n (rst_n),
        .i_wr_start (write_start),
        .i_length (10'd1),
        .i_dwbe (8'h0f),
        .i_data (write_data),
        .i_dw_vld (4'b0001),
        .i_addr (write_address),
        .i_bar_hit (2'd1),
        .o_wr_en (bar1_write_enable),
        .o_wr_addr (bar1_write_address),
        .o_wr_data (bar1_write_data),
        .o_wr_be (bar1_byte_enable),
        .o_wr_bar_hit (write_bar_hit)
    );

    pcie_frame_dma_bridge bridge (
        .pcie_clk (pcie_clk),
        .pcie_rst_n (rst_n),
        .bar1_wr_en (bar1_write_enable && write_bar_hit == 2'd1),
        .bar1_wr_addr (bar1_write_address),
        .bar1_wr_data (bar1_write_data),
        .bar1_wr_byte_en (bar1_byte_enable),
        .ext_dma_mode (dma_mode),
        .ext_mwr64_req (dma_request),
        .ext_mwr64_req_ack (1'b0),
        .ext_mwr_busy (1'b0),
        .ext_req_length (dma_length),
        .ext_req_addr (dma_address),
        .ext_rd_clk_en (1'b0),
        .ext_rd_addr (12'd0),
        .ext_rd_data (),
        .status_data (status_data),
        .ddr_clk (ddr_clk),
        .ddr_rst_n (rst_n),
        .upload_active (upload_active),
        .upload_rd_cmd_en (upload_command),
        .upload_rd_cmd_addr (upload_address),
        .upload_rd_cmd_len (upload_length),
        .upload_rd_cmd_ready (1'b1),
        .upload_rd_cmd_done (upload_done),
        .upload_read_ready (),
        .upload_read_rdata (256'd0),
        .upload_read_en (1'b0),
        .upload_frame_sel (1'b1),
        .upload_frame_ready (1'b1),
        .ddr_axi_arvalid (1'b0),
        .ddr_axi_arready (1'b1)
    );

    task write_register;
        input [63:0] address;
        input [31:0] value;
        integer cycles;
        begin
            @(negedge pcie_clk);
            write_address = address;
            write_data = {96'd0, value};
            write_start = 1'b1;
            @(negedge pcie_clk);
            write_start = 1'b0;
            write_data = 128'd0;
            cycles = 0;
            while (!bar1_write_enable && cycles < 20) begin
                @(negedge pcie_clk);
                cycles = cycles + 1;
            end
            if (!bar1_write_enable || bar1_write_data[31:0] !== value ||
                bar1_byte_enable !== 16'h000f || write_bar_hit !== 2'd1)
                $fatal(1, "Unexpected BAR1 write at %h: enable=%b data=%h be=%h bar=%h",
                       address, bar1_write_enable, bar1_write_data, bar1_byte_enable,
                       write_bar_hit);
            if (bar1_write_address !== address[15:4])
                $fatal(1, "BAR1 write address mismatch at %h", address);
            $display("BAR1 host address %h decoded address=%h data=%h",
                     address, bar1_write_address, value);
            repeat (6) @(negedge pcie_clk);
        end
    endtask

    task check_start;
        input [63:0] bar_base;
        integer cycles;
        begin
            rst_n = 1'b0;
            upload_done = 1'b0;
            repeat (5) @(negedge pcie_clk);
            rst_n = 1'b1;
            repeat (5) @(negedge pcie_clk);
            write_register(bar_base + 64'h160, 32'd1);
            write_register(bar_base + 64'h150, 32'd0);
            if (dma_mode !== 1'b0)
                $fatal(1, "Unexpected DMA start at BAR1 base %h", bar_base);
            write_register(bar_base + 64'h130, HOST_DMA_ADDRESS[31:0]);
            write_register(bar_base + 64'h140, HOST_DMA_ADDRESS[63:32]);
            write_register(bar_base + 64'h150, 32'd1);
            if (dma_mode !== 1'b1 || status_data[2:0] !== 3'b001)
                $fatal(1, "BAR1 start ignored at base %h: status=%h",
                       bar_base, status_data[31:0]);
            cycles = 0;
            while (!upload_command && cycles < 30) begin
                @(negedge ddr_clk);
                cycles = cycles + 1;
            end
            if (!upload_command || !upload_active || upload_address !== 28'h0400000 ||
                upload_length !== 32'd128 || dma_request !== 1'b0)
                $fatal(1, "First DDR read command is incorrect at BAR1 base %h", bar_base);
            @(negedge ddr_clk);
            upload_done = 1'b1;
            @(negedge ddr_clk);
            upload_done = 1'b0;
            cycles = 0;
            while (!dma_request && cycles < 30) begin
                @(negedge pcie_clk);
                cycles = cycles + 1;
            end
            if (!dma_request || dma_length !== 10'd512 || dma_address !== HOST_DMA_ADDRESS)
                $fatal(1, "First DMA request is incorrect at BAR1 base %h: address=%h length=%d",
                       bar_base, dma_address, dma_length);
            $display("BAR1 base %h start, DDR command and DMA address passed", bar_base);
        end
    endtask

    initial begin
        check_start(64'h0000000380904000);
        check_start(64'h0000000000000000);
        check_start(64'h000000038090f000);
        $display("HDMI BAR1 start regression passed");
        $finish;
    end

    initial begin
        #20000;
        $fatal(1, "HDMI BAR1 start simulation timed out");
    end
endmodule
