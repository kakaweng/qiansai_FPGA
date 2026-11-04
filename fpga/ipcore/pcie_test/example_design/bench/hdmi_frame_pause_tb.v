`timescale 1ns/1ps

module hdmi_frame_pause_tb #(
    parameter FRAME_WIDTH = 256,
    parameter FRAME_HEIGHT = 4,
    parameter USE_AXI = 0,
    parameter real PIXEL_HALF_PERIOD = 5.0,
    parameter real DDR_HALF_PERIOD = 3.0,
    parameter LINE_BLANK_CYCLES = 50
);
    reg pixel_clk = 1'b0;
    reg ddr_clk = 1'b0;
    reg reset_n = 1'b0;
    reg frame_sync = 1'b0;
    reg pixel_valid = 1'b0;
    reg [15:0] pixel_data = 16'd0;
    reg pause_request = 1'b0;
    wire write_request;
    wire [27:0] write_address;
    wire [31:0] write_length;
    wire write_ready;
    wire write_done;
    wire write_data_request;
    reg model_write_ready = 1'b1;
    reg model_write_done = 1'b0;
    reg model_write_data_request = 1'b0;
    wire axi_write_ready;
    wire axi_write_done;
    wire axi_write_data_request;
    wire [31:0] camera_bursts;
    wire [31:0] hdmi_bursts;
    wire [255:0] write_data;
    wire [5:0] frame_count;
    wire frame_valid;
    wire frame_select;
    wire frame_paused;
    wire frame_held = pause_request && frame_valid && frame_paused &&
                      write_ready && !write_request;
    reg [15:0] memory [0:1][0:FRAME_WIDTH*FRAME_HEIGHT-1];
    integer write_state = 0;
    integer write_delay = 0;
    integer write_beat = 0;
    integer write_bank = 0;
    integer write_offset = 0;
    integer write_commands = 0;
    integer pixel_index;
    integer source_frame;
    integer held_commands;
    integer held_bank;

    always #(PIXEL_HALF_PERIOD) pixel_clk = ~pixel_clk;
    always #(DDR_HALF_PERIOD) ddr_clk = ~ddr_clk;

    assign write_ready = USE_AXI ? axi_write_ready : model_write_ready;
    assign write_done = USE_AXI ? axi_write_done : model_write_done;
    assign write_data_request = USE_AXI ? axi_write_data_request : model_write_data_request;

    ddr_shared_writer_model axi_writer (
        .clk(ddr_clk), .rst_n(reset_n && USE_AXI),
        .command(write_request), .address(write_address), .length(write_length),
        .command_ready(axi_write_ready), .command_done(axi_write_done),
        .pixels(write_data), .pixel_request(axi_write_data_request),
        .camera_bursts(camera_bursts), .hdmi_bursts(hdmi_bursts)
    );

    wr_buf #(
        .ADDR_WIDTH(28), .H_NUM(FRAME_WIDTH), .V_NUM(FRAME_HEIGHT),
        .DQ_WIDTH(32), .LEN_WIDTH(32), .PIX_WIDTH(16),
        .LINE_ADDR_WIDTH(22), .FRAME_CNT_WIDTH(6)
    ) writer (
        .ddr_clk(ddr_clk), .ddr_rstn(reset_n),
        .wr_clk(pixel_clk), .wr_fsync(frame_sync), .wr_en(pixel_valid),
        .wr_pause(pause_request), .wr_data(pixel_data), .rd_bac(1'b0),
        .ddr_wreq(write_request), .ddr_waddr(write_address),
        .ddr_wr_len(write_length), .ddr_wrdy(write_ready),
        .ddr_wdone(write_done), .ddr_wdata(write_data),
        .ddr_wdata_req(write_data_request), .frame_wcnt(frame_count),
        .frame_wirq(), .frame_valid(frame_valid),
        .frame_sel(frame_select), .frame_paused(frame_paused)
    );

    always @(negedge ddr_clk) begin
        model_write_done = 1'b0;
        if (reset_n && USE_AXI) begin
            if (write_request && write_state == 0) begin
                write_bank = write_address[22];
                write_offset = write_address[21:0] * 2;
                write_beat = 0;
                write_commands = write_commands + 1;
                write_state = 1;
            end
            if (write_done)
                write_state = 0;
        end
        if (reset_n && !USE_AXI) begin
            case (write_state)
                0: if (write_request) begin
                    if (write_length != FRAME_WIDTH / 16)
                        $fatal(1, "Unexpected line length: %0d", write_length);
                    write_bank = write_address[22];
                    write_offset = write_address[21:0] * 2;
                    if (write_offset + FRAME_WIDTH > FRAME_WIDTH * FRAME_HEIGHT)
                        $fatal(1, "Line address escaped the frame: %h", write_address);
                    write_commands = write_commands + 1;
                    model_write_ready = 1'b0;
                    write_delay = 60;
                    write_state = 1;
                end
                1: if (write_delay) begin
                    write_delay = write_delay - 1;
                end else begin
                    write_beat = 0;
                    model_write_data_request = 1'b1;
                    write_state = 2;
                end
                2: if (write_beat == FRAME_WIDTH / 16) begin
                    model_write_data_request = 1'b0;
                    model_write_done = 1'b1;
                    model_write_ready = 1'b1;
                    write_state = 0;
                end
            endcase
        end
    end

    always @(posedge ddr_clk) begin
        if (reset_n && write_data_request) begin
            for (pixel_index = 0; pixel_index < 16; pixel_index = pixel_index + 1)
                memory[write_bank][write_offset + write_beat * 16 + pixel_index] =
                    write_data[pixel_index*16 +: 16];
            write_beat = write_beat + 1;
        end
    end

    task send_frame;
        input integer frame_id;
        input integer pause_position;
        input integer resume_position;
        integer source_row;
        integer source_column;
        begin
            @(negedge pixel_clk);
            frame_sync = 1'b1;
            repeat (8) @(negedge pixel_clk);
            frame_sync = 1'b0;
            repeat (8) @(negedge pixel_clk);
            for (source_row = 0; source_row < FRAME_HEIGHT; source_row = source_row + 1) begin
                for (source_column = 0; source_column < FRAME_WIDTH; source_column = source_column + 1) begin
                    pixel_valid = 1'b1;
                    pixel_data = frame_id * 5123 + source_row * FRAME_WIDTH + source_column;
                    if (source_row * FRAME_WIDTH + source_column == pause_position)
                        pause_request = 1'b1;
                    if (source_row * FRAME_WIDTH + source_column == resume_position)
                        pause_request = 1'b0;
                    @(negedge pixel_clk);
                end
                pixel_valid = 1'b0;
                repeat (LINE_BLANK_CYCLES) @(negedge pixel_clk);
            end
            repeat (50) @(negedge pixel_clk);
            if (USE_AXI) begin
                wait (write_ready && !write_request);
                repeat (4) @(negedge ddr_clk);
            end
        end
    endtask

    task check_frame;
        input integer expected_frame;
        integer check_row;
        integer check_column;
        reg [15:0] expected_pixel;
        begin
            if (!frame_valid)
                $fatal(1, "No complete frame was published: completed_lines=%0d commands=%0d hdmi_bursts=%0d camera_bursts=%0d write_state=%0d",
                       writer.completed_lines, write_commands, hdmi_bursts, camera_bursts, write_state);
            for (check_row = 0; check_row < FRAME_HEIGHT; check_row = check_row + 1)
                for (check_column = 0; check_column < FRAME_WIDTH; check_column = check_column + 1) begin
                    expected_pixel = expected_frame * 5123 + check_row * FRAME_WIDTH + check_column;
                    if (memory[frame_select][check_row * FRAME_WIDTH + check_column] !== expected_pixel)
                        $fatal(1, "Frame shifted/torn: frame=%0d row=%0d column=%0d got=%h expected=%h",
                               expected_frame, check_row, check_column,
                               memory[frame_select][check_row * FRAME_WIDTH + check_column], expected_pixel);
                end
        end
    endtask

    initial begin
        repeat (12) @(negedge pixel_clk);
        reset_n = 1'b1;
        repeat (12) @(negedge pixel_clk);
        if (frame_valid || frame_held)
            $fatal(1, "An incomplete startup frame was published");
        pause_request = 1'b1;
        send_frame(1, -1, -1);
        check_frame(1);
        if (frame_held)
            $fatal(1, "Startup frame was locked before the frame boundary");
        send_frame(15, -1, 93);
        check_frame(1);
        for (source_frame = 2; source_frame <= 12; source_frame = source_frame + 2) begin
            send_frame(source_frame, FRAME_WIDTH * (source_frame % FRAME_HEIGHT) + 37, -1);
            check_frame(source_frame);
            if (frame_held)
                $fatal(1, "Pause acknowledged before the frame boundary");
            held_commands = write_commands;
            held_bank = frame_select;
            send_frame(source_frame + 1, -1, 83 + source_frame * 19);
            if (write_commands != held_commands || frame_select != held_bank)
                $fatal(1, "Writer resumed in the middle of a skipped frame");
            check_frame(source_frame);
        end
        send_frame(14, FRAME_WIDTH * FRAME_HEIGHT - 3, -1);
        send_frame(15, -1, -1);
        if (!frame_held)
            $fatal(1, "A completed paused frame was not available for DMA");
        check_frame(14);
        if (frame_count != 8)
            $fatal(1, "Skipped HDMI frames were counted as captured: %0d", frame_count);
        if (USE_AXI && (camera_bursts < 100 || hdmi_bursts < 8 * FRAME_HEIGHT))
            $fatal(1, "Shared DDR writer did not exercise both sources");
        $display("HDMI frame pause regression passed: width=%0d height=%0d AXI=%0d pixel_period_ns=%0.3f ddr_period_ns=%0.3f, complete RGB565 rows, frame-boundary pause/resume, stable held bank",
                 FRAME_WIDTH, FRAME_HEIGHT, USE_AXI, 2.0 * PIXEL_HALF_PERIOD, 2.0 * DDR_HALF_PERIOD);
        $finish;
    end

    initial begin
        #10000000;
        $fatal(1, "HDMI frame pause simulation timed out");
    end
endmodule

module wr_fram_buf (
    input [31:0] wr_data,
    input [11:0] wr_addr,
    input wr_en, wr_clk, wr_rst,
    input [8:0] rd_addr,
    output reg [255:0] rd_data,
    input rd_clk, rd_rst
);
    reg [31:0] memory [0:4095];
    integer word_index;
    always @(posedge wr_clk)
        if (!wr_rst && wr_en)
            memory[wr_addr] <= wr_data;
    always @(posedge rd_clk)
        if (rd_rst)
            rd_data <= 256'd0;
        else
            for (word_index = 0; word_index < 8; word_index = word_index + 1)
                rd_data[word_index*32 +: 32] <= memory[rd_addr*8 + word_index];
endmodule
