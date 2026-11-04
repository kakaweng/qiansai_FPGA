`timescale 1ns / 1ps

module ddr_write_arbiter #(
    parameter ADDR_WIDTH = 28
)(
    input wire clk,
    input wire rst_n,
    input wire [ADDR_WIDTH-1:0] hdmi_awaddr,
    input wire [3:0] hdmi_awid,
    input wire [3:0] hdmi_awlen,
    input wire [2:0] hdmi_awsize,
    input wire [1:0] hdmi_awburst,
    input wire hdmi_awvalid,
    output wire hdmi_awready,
    input wire [255:0] hdmi_wdata,
    input wire [31:0] hdmi_wstrb,
    input wire hdmi_wvalid,
    output wire hdmi_wready,
    output wire hdmi_wlast,
    input wire [ADDR_WIDTH-1:0] cam_awaddr,
    input wire [3:0] cam_awid,
    input wire [3:0] cam_awlen,
    input wire [2:0] cam_awsize,
    input wire [1:0] cam_awburst,
    input wire cam_awvalid,
    output wire cam_awready,
    input wire [255:0] cam_wdata,
    input wire [31:0] cam_wstrb,
    input wire cam_wvalid,
    output wire cam_wready,
    output wire [ADDR_WIDTH-1:0] axi_awaddr,
    output wire [3:0] axi_awid,
    output wire [3:0] axi_awlen,
    output wire [2:0] axi_awsize,
    output wire [1:0] axi_awburst,
    output wire axi_awvalid,
    input wire axi_awready,
    output wire [255:0] axi_wdata,
    output wire [31:0] axi_wstrb,
    output wire axi_wvalid,
    input wire axi_wready,
    input wire axi_wlast
);
    reg busy;
    reg owner_cam;
    reg address_sent;
    reg last_owner_cam;

    assign axi_awaddr = owner_cam ? cam_awaddr : hdmi_awaddr;
    assign axi_awid = owner_cam ? cam_awid : hdmi_awid;
    assign axi_awlen = owner_cam ? cam_awlen : hdmi_awlen;
    assign axi_awsize = owner_cam ? cam_awsize : hdmi_awsize;
    assign axi_awburst = owner_cam ? cam_awburst : hdmi_awburst;
    assign axi_awvalid = busy && !address_sent && (owner_cam ? cam_awvalid : hdmi_awvalid);
    assign hdmi_awready = busy && !owner_cam && !address_sent && axi_awready;
    assign cam_awready = busy && owner_cam && !address_sent && axi_awready;
    assign axi_wdata = owner_cam ? cam_wdata : hdmi_wdata;
    assign axi_wstrb = owner_cam ? cam_wstrb : hdmi_wstrb;
    assign axi_wvalid = busy && address_sent && (owner_cam ? cam_wvalid : hdmi_wvalid);
    assign hdmi_wready = busy && !owner_cam && address_sent && axi_wready;
    assign hdmi_wlast = busy && !owner_cam && address_sent && axi_wlast;
    assign cam_wready = busy && owner_cam && address_sent && axi_wready;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 1'b0;
            owner_cam <= 1'b0;
            address_sent <= 1'b0;
            last_owner_cam <= 1'b1;
        end else if (!busy) begin
            if (hdmi_awvalid || cam_awvalid) begin
                busy <= 1'b1;
                address_sent <= 1'b0;
                owner_cam <= cam_awvalid && (!hdmi_awvalid || !last_owner_cam);
            end
        end else begin
            if (axi_awvalid && axi_awready)
                address_sent <= 1'b1;
            if (axi_wvalid && axi_wready && axi_wlast) begin
                busy <= 1'b0;
                address_sent <= 1'b0;
                last_owner_cam <= owner_cam;
            end
        end
    end
endmodule
