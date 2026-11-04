`timescale 1ns/1ps
// Isolated BAR1 0x180 command. No video/DMA register is written by this block.
// Dword: 0x4c310000 | (active_low << 9) | (simulation << 8) | (direction << 4) | mode.
// BAR0 0x180: LF01, accepted dword, {live,logical LED bits}, ABI=1.
module drive_feedback_leds #(
    parameter integer CYCLES_PER_MS = 125000,
    parameter integer LEASE_MS = 1500
)(
    input wire clk, input wire rst_n,
    input wire wr_en, input wire [11:0] wr_addr,
    input wire [127:0] wr_data, input wire [15:0] wr_byte_en,
    output wire [2:0] led_on, output wire [127:0] status
);
    localparam integer DIV_BITS = (CYCLES_PER_MS > 1) ? $clog2(CYCLES_PER_MS) : 1;
    localparam integer AGE_BITS = $clog2(LEASE_MS + 1);
    reg [DIV_BITS-1:0] divider;
    reg [AGE_BITS-1:0] age_ms;
    reg [9:0] phase_ms;
    reg [31:0] command;
    reg seen;
    reg [2:0] logical_leds;
    wire ms_tick = (divider == CYCLES_PER_MS - 1);
    wire [31:0] incoming = wr_data[31:0];
    // RX retains host address bits [15:12] in wr_addr[11:8]. BAR1 hit is
    // already qualified upstream; decode the 4KiB offset exactly like DMA.
    wire valid_write = wr_en && wr_addr[7:0] == 8'h18 && wr_byte_en[3:0] == 4'hf &&
        (incoming & 32'hfffffcc0) == 32'h4c310000 && incoming[3:0] <= 6 &&
        ((incoming[3:0] != 1 && incoming[3:0] != 2) || incoming[5:4] != 0);
    wire live = seen && age_ms < LEASE_MS;
    wire [3:0] mode = !seen || command[3:0] == 0 ? 4'd0 : !live ? 4'd5 : command[3:0];
    wire [2:0] selected = command[5:4] == 1 ? 3'b001 :
                          command[5:4] == 2 ? 3'b010 :
                          command[5:4] == 3 ? 3'b100 : 3'b000;
    assign led_on = logical_leds ^ {3{command[9]}};
    assign status = {32'd1, 23'd0, live, 5'd0, logical_leds, command, 32'h4c463031};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            divider <= 0; age_ms <= LEASE_MS; phase_ms <= 0;
            command <= 0; seen <= 0;
        end else begin
            if (ms_tick) begin
                divider <= 0;
                phase_ms <= phase_ms == 999 ? 0 : phase_ms + 1'b1;
                if (age_ms < LEASE_MS) age_ms <= age_ms + 1'b1;
            end else divider <= divider + 1'b1;
            if (valid_write) begin
                command <= incoming; seen <= 1; age_ms <= 0;
                if (!live || incoming != command) phase_ms <= 0;
            end
        end
    end
    always @* begin
        logical_leds = 0;
        case (mode)
            1: logical_leds = phase_ms < 500 ? selected : 3'b000; // request acknowledged
            2: logical_leds = selected;                          // go / simulated movement
            3: logical_leds = 3'b111;                            // stop
            4: logical_leds = (phase_ms < 100 || (phase_ms >= 300 && phase_ms < 400)) ? 3'b111 : 3'b000;
            5: logical_leds = phase_ms < 500 ? 3'b111 : 3'b000;   // unknown / stale
            6: logical_leds = (phase_ms < 125 || (phase_ms >= 250 && phase_ms < 375) ||
                              (phase_ms >= 500 && phase_ms < 625) ||
                              (phase_ms >= 750 && phase_ms < 875)) ? 3'b111 : 3'b000; // alarm, 4Hz
            default: logical_leds = 0;
        endcase
    end
endmodule
