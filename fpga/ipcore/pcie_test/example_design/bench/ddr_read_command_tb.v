`timescale 1ns/1ps

module ddr_read_command_tb;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg command = 1'b0;
    reg [27:0] command_address = 28'h1000;
    reg [31:0] command_length = 32'd120;
    reg data_valid = 1'b0;
    reg burst_done = 1'b0;
    wire command_ready;
    wire command_done;
    wire burst_request;
    wire [27:0] burst_address;
    wire [3:0] burst_length;
    integer burst_index;
    integer beat_index;
    integer cycles;
    integer total_beats = 0;
    integer completions = 0;

    always #5 clk = ~clk;

    wr_cmd_trans controller (
        .clk (clk), .rstn (rst_n),
        .wr_cmd_en (1'b0), .wr_cmd_addr (28'd0), .wr_cmd_len (32'd0),
        .wr_cmd_ready (), .wr_cmd_done (), .wr_bac (1'b0),
        .wr_ctrl_data (128'd0), .wr_data_re (), .wr_en (), .wr_addr (),
        .wr_id (), .wr_len (), .wr_data_en (), .wr_data (),
        .wr_ready (1'b0), .wr_done (1'b0),
        .rd_cmd_en (command), .rd_cmd_addr (command_address),
        .rd_cmd_len (command_length), .rd_cmd_ready (command_ready),
        .rd_cmd_done (command_done), .read_en (data_valid),
        .rd_en (burst_request), .rd_addr (burst_address), .rd_id (),
        .rd_len (burst_length), .rd_done_p (burst_done)
    );

    always @(posedge clk) begin
        if (rst_n && data_valid)
            total_beats = total_beats + 1;
        if (rst_n && command_done) begin
            completions = completions + 1;
            if (total_beats != 120)
                $fatal(1, "Read completed early: received=%0d", total_beats);
        end
    end

    initial begin
        repeat (6) @(negedge clk);
        rst_n = 1'b1;
        repeat (4) @(negedge clk);
        command = 1'b1;
        @(negedge clk);
        command = 1'b0;
        command_address = 28'h400000;
        command_length = 32'd128;
        for (burst_index = 0; burst_index < 8; burst_index = burst_index + 1) begin
            cycles = 0;
            while (!burst_request && cycles < 30) begin
                @(negedge clk);
                cycles = cycles + 1;
            end
            if (!burst_request || burst_address !== 28'h1000 + burst_index*128 ||
                burst_length !== ((burst_index == 7) ? 4'd7 : 4'd15))
                $fatal(1, "Active read used a changed command: burst=%0d addr=%h len=%0d",
                       burst_index, burst_address, burst_length);
            for (beat_index = 0; beat_index <= burst_length; beat_index = beat_index + 1) begin
                if (burst_index == 7 && beat_index == 7) begin
                    data_valid = 1'b0;
                    repeat (12) @(negedge clk);
                    if (command_done || completions)
                        $fatal(1, "Read finished while the final beat was delayed");
                end
                data_valid = 1'b1;
                @(negedge clk);
            end
            data_valid = 1'b0;
            burst_done = 1'b1;
            @(negedge clk);
            burst_done = 1'b0;
        end
        repeat (8) @(negedge clk);
        if (!command_ready || total_beats != 120 || completions != 1)
            $fatal(1, "Read did not recover: ready=%b beats=%0d completions=%0d",
                   command_ready, total_beats, completions);
        $display("DDR read command regression passed: latched address/length and final-beat completion");
        $finish;
    end

    initial begin
        #20000;
        $fatal(1, "DDR read command simulation timed out");
    end
endmodule
