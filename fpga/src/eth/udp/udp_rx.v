module udp_rx #(
    parameter BOARD_MAC = 48'h00_11_22_33_44_55,
    parameter BOARD_IP = {8'd192,8'd168,8'd1,8'd10}
)(
    input wire clk, rst_n,
    input wire gmii_rx_dv,
    input wire [7:0] gmii_rxd,
    output reg rec_pkt_done,
    output reg rec_en,
    output reg [7:0] rec_data,
    output reg [15:0] rec_byte_num
);
    localparam IDLE=0, PREAMBLE=1, ETH=2, IP=3, UDP=4, DATA=5, DRAIN=6;
    reg [2:0] state;
    reg [15:0] index, ip_length, udp_length, remaining;
    reg [47:0] destination_mac;
    reg [31:0] destination_ip;
    reg [15:0] eth_type;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            index <= 0;
            ip_length <= 0;
            udp_length <= 0;
            remaining <= 0;
            destination_mac <= 0;
            destination_ip <= 0;
            eth_type <= 0;
            rec_pkt_done <= 0;
            rec_en <= 0;
            rec_data <= 0;
            rec_byte_num <= 0;
        end else begin
            rec_pkt_done <= 0;
            rec_en <= 0;
            if (!gmii_rx_dv) begin
                state <= IDLE;
                index <= 0;
                remaining <= 0;
            end else begin
                index <= index + 1'b1;
                case (state)
                    IDLE: begin
                        index <= 1;
                        if (gmii_rxd == 8'h55) state <= PREAMBLE;
                        else state <= DRAIN;
                    end
                    PREAMBLE: begin
                        if (index < 7 && gmii_rxd != 8'h55) state <= DRAIN;
                        else if (index == 7) begin
                            index <= 0;
                            state <= gmii_rxd == 8'hd5 ? ETH : DRAIN;
                        end
                    end
                    ETH: begin
                        if (index < 6) destination_mac <= {destination_mac[39:0], gmii_rxd};
                        if (index == 12) eth_type[15:8] <= gmii_rxd;
                        if (index == 13) begin
                            index <= 0;
                            if ((destination_mac == BOARD_MAC || destination_mac == 48'hffffffffffff) &&
                                eth_type[15:8] == 8'h08 && gmii_rxd == 0) state <= IP;
                            else state <= DRAIN;
                        end
                    end
                    IP: begin
                        if (index == 0 && gmii_rxd != 8'h45) state <= DRAIN;
                        if (index == 2) ip_length[15:8] <= gmii_rxd;
                        if (index == 3) ip_length[7:0] <= gmii_rxd;
                        if (index == 6 && (gmii_rxd & 8'hbf) != 0) state <= DRAIN;
                        if (index == 7 && gmii_rxd != 0) state <= DRAIN;
                        if (index == 9 && gmii_rxd != 17) state <= DRAIN;
                        if (index >= 16 && index <= 18)
                            destination_ip <= {destination_ip[23:0], gmii_rxd};
                        if (index == 19) begin
                            index <= 0;
                            state <= {destination_ip[23:0], gmii_rxd} == BOARD_IP &&
                                     ip_length >= 29 && ip_length <= 1500 ? UDP : DRAIN;
                        end
                    end
                    UDP: begin
                        if (index == 4) udp_length[15:8] <= gmii_rxd;
                        if (index == 5) udp_length[7:0] <= gmii_rxd;
                        if (index == 7) begin
                            index <= 0;
                            remaining <= udp_length - 8;
                            rec_byte_num <= udp_length - 8;
                            state <= udp_length >= 9 && udp_length + 20 == ip_length ? DATA : DRAIN;
                        end
                    end
                    DATA: begin
                        rec_data <= gmii_rxd;
                        rec_en <= 1;
                        remaining <= remaining - 1'b1;
                        if (remaining == 1) begin
                            rec_pkt_done <= 1;
                            state <= DRAIN;
                        end
                    end
                    default: state <= DRAIN;
                endcase
            end
        end
    end
endmodule
