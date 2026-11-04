`timescale 1ns/1ps

module pcie_hdmi_status_tb;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg read_enable = 1'b0;
    reg [9:0] read_length = 10'd1;
    reg [63:0] read_address = 64'd0;
    reg transmit_enable = 1'b0;
    reg transmit_hold = 1'b0;
    reg [127:0] ram_data = 128'd0;
    wire ram_read_enable;
    wire [11:0] ram_read_address;
    wire data_valid;
    wire [127:0] read_data;
    wire last_data;
    localparam [127:0] STATUS_DATA = {32'd0, 32'h46524d45, 32'd2073600, 32'd0};
    localparam [127:0] RAM_WORD_ZERO = 128'hdcba9876543210fedcba987654321000;
    localparam [127:0] RAM_WORD_ONE = 128'hfedcba98765432100123456789abcdef;

    always #4 clk = ~clk;

    always @(posedge clk)
        if (ram_read_enable)
            ram_data <= ram_read_address == 12'd0 ? RAM_WORD_ZERO : RAM_WORD_ONE;

    ipsl_pcie_dma dut (
        .clk (clk),
        .rst_n (rst_n),
        .i_axis_master_tvld (1'b0),
        .i_axis_master_tdata (128'd0),
        .i_axis_master_tkeep (4'd0),
        .i_axis_master_tlast (1'b0),
        .i_axis_master_tuser (8'd0),
        .i_axis_slave0_trdy (1'b1),
        .i_axis_slave1_trdy (1'b1),
        .i_axis_slave2_trdy (1'b1),
        .i_ext_dma_mode (1'b0),
        .i_ext_status_data (STATUS_DATA)
    );

    ipsl_pcie_dma_rd_ctrl #(.ADDR_WIDTH (12)) read_controller (
        .clk (clk),
        .rst_n (rst_n),
        .i_rd_en (read_enable),
        .i_rd_length (read_length),
        .i_rd_addr (read_address),
        .i_tx_hold (transmit_hold),
        .i_tlp_tx (transmit_enable),
        .o_rd_ram_hold (),
        .o_gen_tlp_start (data_valid),
        .o_rd_data (read_data),
        .o_last_data (last_data),
        .o_bar_rd_clk_en (ram_read_enable),
        .o_bar_rd_addr (ram_read_address),
        .i_bar_rd_data (dut.bar0_rd_data)
    );

    task check_read;
        input [63:0] address;
        input [9:0] length;
        input [127:0] expected;
        input [127:0] mask;
        integer cycles;
        begin
            @(negedge clk);
            read_address = address;
            read_length = length;
            read_enable = 1'b1;
            @(negedge clk);
            read_enable = 1'b0;
            cycles = 0;
            while (!data_valid && cycles < 100) begin
                @(negedge clk);
                cycles = cycles + 1;
            end
            if (!data_valid)
                $fatal(1, "BAR0 read timed out at offset %h", address);
            if ((read_data & mask) !== (expected & mask))
                $fatal(1, "BAR0 offset %h expected %h got %h",
                       address, expected & mask, read_data & mask);
            transmit_hold = 1'b1;
            repeat (4) begin
                @(negedge clk);
                if (!data_valid || (read_data & mask) !== (expected & mask))
                    $fatal(1, "BAR0 data changed while held at offset %h", address);
            end
            transmit_hold = 1'b0;
            transmit_enable = 1'b1;
            @(negedge clk);
            transmit_enable = 1'b0;
            repeat (8) @(negedge clk);
            $display("BAR0 offset %h passed", address);
        end
    endtask

    initial begin
        force dut.bar0_rd_clk_en = ram_read_enable;
        force dut.bar0_rd_addr = ram_read_address;
        force dut.bar0_ram_rd_data = ram_data;
        repeat (5) @(negedge clk);
        rst_n = 1'b1;
        repeat (5) @(negedge clk);
        check_read(64'h0, 10'd1, 128'd0, 128'hffffffff);
        check_read(64'h4, 10'd1, 128'd2073600, 128'hffffffff);
        check_read(64'h8, 10'd1, 128'h46524d45, 128'hffffffff);
        check_read(64'hc, 10'd1, 128'd0, 128'hffffffff);
        check_read(64'h10, 10'd4, RAM_WORD_ONE, {128{1'b1}});
        check_read(64'h0, 10'd4, STATUS_DATA, {128{1'b1}});
        check_read(64'hc, 10'd2, {64'd0, RAM_WORD_ONE[31:0], 32'd0},
                   128'hffffffffffffffff);
        $display("HDMI BAR0 status read regression passed");
        $finish;
    end

    initial begin
        #20000;
        $fatal(1, "Simulation timed out");
    end
endmodule

module ipsl_pcie_dma_ram (
    input [127:0] wr_data,
    input [11:0] wr_addr,
    input wr_en,
    input wr_clk,
    input wr_rst,
    input [15:0] wr_byte_en,
    output reg [127:0] rd_data,
    input [11:0] rd_addr,
    input rd_clk,
    input rd_clk_en,
    input rd_rst
);
    reg [127:0] memory [0:4095];
    integer byte_index;

    always @(posedge wr_clk)
        if (wr_en && !wr_rst)
            for (byte_index = 0; byte_index < 16; byte_index = byte_index + 1)
                if (wr_byte_en[byte_index])
                    memory[wr_addr][byte_index*8 +: 8] <= wr_data[byte_index*8 +: 8];

    always @(posedge rd_clk or posedge rd_rst)
        if (rd_rst)
            rd_data <= 128'd0;
        else if (rd_clk_en)
            rd_data <= memory[rd_addr];
endmodule
