`timescale 1ns / 1ps

module ethernet_video_rx #(
    parameter FRAME_BYTES = 614400,
    parameter PACKET_BYTES = 1024
)(
    input wire clk,
    input wire rst_n,
    input wire rx_en,
    input wire [7:0] rx_data,
    input wire rx_done,
    input wire [15:0] rx_bytes,
    output wire token_valid,
    input wire token_ready,
    output wire [258:0] token_data,
    output reg [31:0] packets_good,
    output reg [31:0] packets_bad
);
    localparam WORDS = PACKET_BYTES / 32;
    localparam IDLE = 0, START = 1, DATA = 2, END_FRAME = 3;
    reg [255:0] packet_memory [0:WORDS-1];
    reg [255:0] packed_bytes;
    reg [15:0] byte_index;
    reg [31:0] magic, packet_frame, packet_offset, packet_frame_bytes;
    reg [15:0] packet_length, flags;
    reg [31:0] expected_crc, crc;
    reg packet_overrun;
    reg receiving;
    reg active;
    reg [31:0] active_frame, next_offset;
    reg [2:0] emit_state;
    reg [15:0] emit_word;
    reg [31:0] emit_frame;
    reg emit_last;

    function [31:0] crc_byte;
        input [31:0] previous;
        input [7:0] value;
        integer bit_index;
        reg [31:0] current;
        begin
            current = previous ^ value;
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
                current = current[0] ? (current >> 1) ^ 32'hedb88320 : current >> 1;
            crc_byte = current;
        end
    endfunction

    assign token_valid = emit_state != IDLE;
    assign token_data = emit_state == START ? {1'b0, 2'd0, 224'd0, emit_frame} :
                        emit_state == END_FRAME ? {1'b0, 2'd2, 224'd0, emit_frame} :
                        {1'b0, 2'd1, packet_memory[emit_word]};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            packed_bytes <= 0;
            byte_index <= 0;
            magic <= 0;
            packet_frame <= 0;
            packet_offset <= 0;
            packet_frame_bytes <= 0;
            packet_length <= 0;
            flags <= 0;
            expected_crc <= 0;
            crc <= 32'hffffffff;
            packet_overrun <= 0;
            receiving <= 0;
            active <= 0;
            active_frame <= 0;
            next_offset <= 0;
            emit_state <= IDLE;
            emit_word <= 0;
            emit_frame <= 0;
            emit_last <= 0;
            packets_good <= 0;
            packets_bad <= 0;
        end else begin
            if (token_valid && token_ready) begin
                case (emit_state)
                    START: emit_state <= DATA;
                    DATA: begin
                        if (emit_word == WORDS-1) begin
                            emit_state <= emit_last ? END_FRAME : IDLE;
                        end else emit_word <= emit_word + 1'b1;
                    end
                    END_FRAME: emit_state <= IDLE;
                    default: emit_state <= IDLE;
                endcase
            end
            if (!rx_en) begin
                if (receiving) begin
                    receiving <= 0;
                    byte_index <= 0;
                    if (!rx_done) begin
                        active <= 0;
                        packets_bad <= packets_bad + 1'b1;
                    end
                end
                packet_overrun <= 0;
                crc <= 32'hffffffff;
            end else begin
                receiving <= 1;
                byte_index <= byte_index == 16'hffff ? byte_index : byte_index + 1'b1;
                if (!receiving) begin
                    byte_index <= 1;
                    packet_overrun <= emit_state != IDLE;
                    if (emit_state != IDLE) begin
                        emit_state <= IDLE;
                        active <= 0;
                    end
                end
                if (byte_index < 20 || byte_index >= 24)
                    crc <= crc_byte(crc, rx_data);
                case (byte_index)
                    0,1,2,3: magic <= {magic[23:0], rx_data};
                    4,5,6,7: packet_frame <= {packet_frame[23:0], rx_data};
                    8,9,10,11: packet_offset <= {packet_offset[23:0], rx_data};
                    12,13: packet_length <= {packet_length[7:0], rx_data};
                    14,15: flags <= {flags[7:0], rx_data};
                    16,17,18,19: packet_frame_bytes <= {packet_frame_bytes[23:0], rx_data};
                    20,21,22,23: expected_crc <= {expected_crc[23:0], rx_data};
                    default: begin
                        if (byte_index >= 24 && byte_index < 24 + PACKET_BYTES) begin
                            packed_bytes[((byte_index-24) % 32)*8 +: 8] <= rx_data;
                            if ((byte_index-24) % 32 == 31)
                                packet_memory[(byte_index-24)/32] <= {rx_data, packed_bytes[247:0]};
                        end
                    end
                endcase
            end
            if (rx_done) begin
                receiving <= 0;
                byte_index <= 0;
                crc <= 32'hffffffff;
                if (rx_en && !packet_overrun && emit_state == IDLE &&
                    byte_index == 23 + PACKET_BYTES && rx_bytes == 24 + PACKET_BYTES &&
                    magic == 32'h45564631 && flags == 0 &&
                    packet_length == PACKET_BYTES && packet_frame_bytes == FRAME_BYTES &&
                    packet_offset < FRAME_BYTES && packet_offset % PACKET_BYTES == 0 &&
                    ~crc_byte(crc, rx_data) == expected_crc &&
                    (packet_offset == 0 || (active && packet_frame == active_frame && packet_offset == next_offset))) begin
                    packets_good <= packets_good + 1'b1;
                    active <= packet_offset + PACKET_BYTES < FRAME_BYTES;
                    active_frame <= packet_frame;
                    next_offset <= packet_offset + PACKET_BYTES;
                    emit_state <= packet_offset == 0 ? START : DATA;
                    emit_word <= 0;
                    emit_frame <= packet_frame;
                    emit_last <= packet_offset + PACKET_BYTES == FRAME_BYTES;
                end else begin
                    if (magic == 32'h45564631) begin
                        packets_bad <= packets_bad + 1'b1;
                        active <= 0;
                    end
                end
            end
        end
    end
endmodule
