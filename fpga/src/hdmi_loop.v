`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company:Meyesemi 
// Engineer: Will
// 
// Create Date: 2023-01-29 20:31  
// Design Name:  
// Module Name: 
// Project Name: 
// Target Devices: Pango
// Tool Versions: 
// Description: 
//      
// Dependencies: 
// 
// Revision:
// Revision 1.0 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////
`define UD #1

module hdmi_loop #(
    parameter PCIE_RGB565 = 1,
    parameter CAM_VSYNC_ACTIVE_HIGH = 1,
    parameter CAM_HIGH_BYTE_FIRST = 1,
    parameter CAMERA_DDR_PREFETCH = 1,
    parameter ETHERNET_VIDEO = 1,
    parameter JUNCTION_KEYS = 1
)(
    input wire        sys_clk,     // input system clock 50MHz
    input wire        key_mode,    // KEY1: original HDMI image mode (including lowlight)
    input wire        key_th_inc,  // KEY2: original threshold up
    input wire        key_th_dec,  // KEY3: original threshold down
    input wire        key_upload,  // KEY4, active low, toggle UDP video stream
    input wire        key_route_left,     // KEY5 / J16: junction left
    input wire        key_route_straight, // KEY6 / J19: junction straight
    input wire        key_route_right,    // KEY7 / H20: junction right
    input wire        eth_rxc,
    input wire        eth_rx_ctl,
    input wire [3:0]  eth_rxd,
    output wire       eth_txc,
    output wire       eth_tx_ctl,
    output wire [3:0] eth_txd,
    input wire        cam_pclk,
    input wire        cam_vsync,
    input wire        cam_href,
    input wire [7:0]  cam_data,
    output wire       cam_rst_n,
    output wire       cam_scl,
    inout  wire       cam_sda,

    output wire       mem_rst_n,
    output wire       mem_ck,
    output wire       mem_ck_n,
    output wire       mem_cke,
    output wire       mem_cs_n,
    output wire       mem_ras_n,
    output wire       mem_cas_n,
    output wire       mem_we_n,
    output wire       mem_odt,
    output wire [14:0] mem_a,
    output wire [2:0]  mem_ba,
    inout  wire [3:0]  mem_dqs,
    inout  wire [3:0]  mem_dqs_n,
    inout  wire [31:0] mem_dq,
    output wire [3:0]  mem_dm,
    output wire       ddr_init_done,
    
    output            rstn_out,
    output            iic_scl,
    inout             iic_sda, 
    output            iic_tx_scl,
    inout             iic_tx_sda, 
    input             pixclk_in,                            
    input             vs_in, 
    input             hs_in, 
    input             de_in,
    input     [7:0]   r_in, 
    input     [7:0]   g_in, 
    input     [7:0]   b_in,  

    output               pixclk_out,                            
    output reg           vs_out, 
    output reg           hs_out, 
    output reg           de_out,
    output reg    [7:0]  r_out, 
    output reg    [7:0]  g_out, 
    output reg    [7:0]  b_out,
    output               led_int,
    output wire [2:0]    drive_led, // A2/B3/C5: left/straight/right business feedback

    // PCIe endpoint interface (to RK3568/DAYU200)
    input                pcie_perst_n,
    input                pcie_ref_clk_n,
    input                pcie_ref_clk_p,
    input       [1:0]    pcie_rxn,
    input       [1:0]    pcie_rxp,
    output      [1:0]    pcie_txn,
    output      [1:0]    pcie_txp
);

    parameter   X_WIDTH = 4'd12;
    parameter   Y_WIDTH = 4'd12;    
//MODE_1080p
    parameter V_TOTAL = 12'd1125;
    parameter V_FP = 12'd4;
    parameter V_BP = 12'd36;
    parameter V_SYNC = 12'd5;
    parameter V_ACT = 12'd1080;
    parameter H_TOTAL = 12'd2200;
    parameter H_FP = 12'd88;
    parameter H_BP = 12'd148;
    parameter H_SYNC = 12'd44;
    parameter H_ACT = 12'd1920;
    parameter HV_OFFSET = 12'd0;

    // Keep the existing HDMI design unchanged and reuse the validated
    // PCIe example endpoint/DMA implementation.
    wire pcie_ref_led;
    wire pcie_pclk_led;
    wire pcie_pclk_div2_led;
    wire pcie_smlh_link_up;
    wire pcie_rdlh_link_up;
    wire pcie_txd_unused;
    wire pcie_frame_dma_clk;
    wire pcie_frame_dma_rst_n;
    wire pcie_frame_bar1_wr_en;
    wire [11:0] pcie_frame_bar1_wr_addr;
    wire [127:0] pcie_frame_bar1_wr_data;
    wire [15:0] pcie_frame_bar1_wr_byte_en;
    wire pcie_frame_dma_mode;
    wire pcie_frame_mwr64_req;
    wire pcie_frame_mwr64_req_ack;
    wire pcie_frame_mwr_busy;
    wire [9:0] pcie_frame_req_length;
    wire [63:0] pcie_frame_req_addr;
    wire pcie_frame_rd_clk_en;
    wire [11:0] pcie_frame_rd_addr;
    wire [127:0] pcie_frame_rd_data;
    wire [127:0] pcie_frame_status_data;
    wire [895:0] pcie_video_info_data;
    wire [127:0] pcie_junction_keys;
    wire [127:0] pcie_drive_feedback;
    drive_feedback_leds u_drive_feedback (
        .clk(pcie_frame_dma_clk), .rst_n(pcie_frame_dma_rst_n),
        .wr_en(pcie_frame_bar1_wr_en), .wr_addr(pcie_frame_bar1_wr_addr),
        .wr_data(pcie_frame_bar1_wr_data), .wr_byte_en(pcie_frame_bar1_wr_byte_en),
        .led_on(drive_led), .status(pcie_drive_feedback)
    );
    junction_key_input u_junction_keys (
        .clk(pcie_frame_dma_clk), .rst_n(pcie_frame_dma_rst_n),
        .keys_n({key_route_right, key_route_straight, key_route_left}),
        .status(pcie_junction_keys)
    );

    pango_pcie_top u_pango_pcie_top (
        .button_rst_n  (1'b1),
        .perst_n       (pcie_perst_n),
        .free_clk      (sys_clk),
        .txd           (pcie_txd_unused),
        .rxd           (1'b1),
        .ref_clk_n     (pcie_ref_clk_n),
        .ref_clk_p     (pcie_ref_clk_p),
        .rxn           (pcie_rxn),
        .rxp           (pcie_rxp),
        .txn           (pcie_txn),
        .txp           (pcie_txp),
        .ref_led       (pcie_ref_led),
        .pclk_led      (pcie_pclk_led),
        .pclk_div2_led (pcie_pclk_div2_led),
        .smlh_link_up  (pcie_smlh_link_up),
        .rdlh_link_up  (pcie_rdlh_link_up),
        .frame_dma_clk          (pcie_frame_dma_clk),
        .frame_dma_rst_n        (pcie_frame_dma_rst_n),
        .frame_bar1_wr_en       (pcie_frame_bar1_wr_en),
        .frame_bar1_wr_addr     (pcie_frame_bar1_wr_addr),
        .frame_bar1_wr_data     (pcie_frame_bar1_wr_data),
        .frame_bar1_wr_byte_en  (pcie_frame_bar1_wr_byte_en),
        .frame_dma_mode         (pcie_frame_dma_mode),
        .frame_mwr64_req        (pcie_frame_mwr64_req),
        .frame_mwr64_req_ack    (pcie_frame_mwr64_req_ack),
        .frame_mwr_busy         (pcie_frame_mwr_busy),
        .frame_req_length       (pcie_frame_req_length),
        .frame_req_addr         (pcie_frame_req_addr),
        .frame_rd_clk_en        (pcie_frame_rd_clk_en),
        .frame_rd_addr          (pcie_frame_rd_addr),
        .frame_rd_data          (pcie_frame_rd_data),
        .frame_status_data      (pcie_frame_status_data),
        .video_info_data        (pcie_video_info_data),
        .junction_keys_data     (JUNCTION_KEYS ? pcie_junction_keys : 128'd0),
        .drive_feedback_data    (pcie_drive_feedback)
    );

    localparam [47:0] BOARD_MAC = 48'h00_11_22_33_44_55;
    localparam [31:0] BOARD_IP  = {8'd192, 8'd168, 8'd1, 8'd10};
    localparam [47:0] DES_MAC   = 48'hff_ff_ff_ff_ff_ff;
    localparam [31:0] DES_IP    = {8'd192, 8'd168, 8'd1, 8'd102};
    localparam        MEM_ROW_WIDTH    = 15;
    localparam        MEM_COLUMN_WIDTH = 10;
    localparam        MEM_BANK_WIDTH   = 3;
    localparam        MEM_DQ_WIDTH     = 32;
    localparam        CTRL_ADDR_WIDTH  = MEM_ROW_WIDTH + MEM_BANK_WIDTH + MEM_COLUMN_WIDTH;

    reg [15:0]  rstn_1ms       ;
    wire        cfg_clk        ;
    wire        locked         ;
    wire        init_over      ;
    wire        gray_vs        ;
    wire        gray_hs        ;
    wire        gray_de        ;
    wire [7:0]  gray_r         ;
    wire [7:0]  gray_g         ;
    wire [7:0]  gray_b         ;
    wire [7:0]  gray_y         ;
    wire        median_vs      ;
    wire        median_hs      ;
    wire        median_de      ;
    wire [7:0]  median_y       ;
    wire        edge_vs        ;
    wire        edge_hs        ;
    wire        edge_de        ;
    wire        edge_bit       ;
    wire [7:0]  edge_data      ;
    wire        udp_pix_vs     ;
    wire        udp_pix_de     ;
    wire [7:0]  udp_pix_data   ;
    wire        udp_bin_data   ;
    wire        hdmi_pix_vs    ;
    wire        hdmi_pix_hs    ;
    wire        hdmi_pix_de    ;
    wire        hdmi_bin_data  ;
    wire [7:0]  hdmi_gray_data ;
    wire [7:0]  hdmi_pix_data  ;
    wire        hdmi_raw_mode   ;
    wire        hdmi_lowlight_mode;
    wire [1:0]  lowlight_strength;
    wire        lowlight_vs,lowlight_hs,lowlight_de;
    wire [7:0]  lowlight_r_out,lowlight_g_out,lowlight_b_out;
    wire        hdmi_ddr_show_ready;
    wire [2:0]  preprocess_mode;
    wire [7:0]  bin_threshold  ;
    wire        eth_tx_clk     ;
    wire        eth_rx_clk     ;
    wire        udp_tx_start_en;
    wire        udp_tx_start_en_gray;
    wire        udp_tx_start_en_rgb;
    wire        udp_tx_start_en_cam;
    wire [7:0]  udp_tx_data    ;
    wire [7:0]  udp_tx_data_gray;
    wire [7:0]  udp_tx_data_rgb;
    wire [7:0]  udp_tx_data_cam;
    wire [15:0] udp_tx_byte_num;
    wire [15:0] udp_tx_byte_num_gray;
    wire [15:0] udp_tx_byte_num_rgb;
    wire [15:0] udp_tx_byte_num_cam;
    wire        udp_tx_done    ;
    wire        udp_tx_req     ;
    wire        udp_rec_pkt_done;
    wire        udp_rec_en     ;
    wire [7:0]  udp_rec_data   ;
    wire [15:0] udp_rec_byte_num;
    wire        rgb_pix_vs     ;
    wire        rgb_pix_de     ;
    wire [7:0]  rgb_pix_r      ;
    wire [7:0]  rgb_pix_g      ;
    wire [7:0]  rgb_pix_b      ;
    wire        upload_key_pulse;
    wire        udp_capture_busy;
    wire        udp_capture_busy_gray;
    wire        udp_capture_busy_rgb;
    wire        cam_pll_locked;
    wire        cam_initial_en;
    wire        cmos_init_done;
    wire        cam_capture_configured;
    wire        cam_capture_active;
    (* PAP_MARK_DEBUG = "true" *) wire cam_frame_start;
    (* PAP_MARK_DEBUG = "true" *) wire cam_frame_end;
    (* PAP_MARK_DEBUG = "true" *) wire cam_line_start;
    (* PAP_MARK_DEBUG = "true" *) wire cam_line_end;
    (* PAP_MARK_DEBUG = "true" *) wire cam_pixel_valid;
    (* PAP_MARK_DEBUG = "true" *) wire [15:0] cam_pixel_data;
    (* PAP_MARK_DEBUG = "true" *) wire cam_frame_valid;
    (* PAP_MARK_DEBUG = "true" *) wire cam_frame_error;
    wire cam_frame_seen;
    wire cam_last_frame_valid;
    wire [7:0] cam_last_error_flags;
    wire [31:0] cam_good_frames;
    wire [31:0] cam_bad_frames;
    wire [31:0] cam_last_pixel_count;
    wire [15:0] cam_last_line_count;
    wire [15:0] cam_last_line_bytes;
    wire [31:0] cam_last_pixel_sum;
    wire [15:0] cam_last_first_pixel;
    wire [15:0] cam_last_last_pixel;
    wire        clock_20k;
    wire        cam_transfer_cmd;
    wire        cam_transfer_cmd_sync;
    wire        cam_cfg_rst_n;
    wire        cam_udp_tx_done;
    wire        cam_udp_tx_req;
    wire        hdmi_udp_tx_start_en;
    wire [7:0]  hdmi_udp_tx_data;
    wire [15:0] hdmi_udp_tx_byte_num;
    wire        hdmi_udp_tx_done;
    wire        hdmi_udp_tx_req;
    wire [8:0]  cam_reg_index;
    wire        clk_25m_cam;
    wire        ddr_core_clk    ;
    wire        ddr_pll_lock    ;
    wire        ddr_fb_init_done;
    wire        ddr_upload_frame_ready;
    wire        ddr_vout_de     ;
    wire [15:0] ddr_vout_data   ;
    wire [15:0] ddr_wr_data     ;
    wire        ddr_video_ready ;
    wire        ddr_display_ready;
    wire        ddr_upload_active;
    wire [1:0]  ddr_upload_source;
    wire ethernet_frame_ready;
    wire [CTRL_ADDR_WIDTH-1:0] ethernet_frame_base;
    wire [127:0] ethernet_status, ethernet_status_pcie;
    wire [CTRL_ADDR_WIDTH-1:0] ethernet_awaddr, camera_eth_awaddr;
    wire ethernet_awvalid, ethernet_awready, ethernet_wvalid, ethernet_wready;
    wire [255:0] ethernet_wdata, camera_eth_wdata;
    wire [3:0] camera_eth_awid, camera_eth_awlen;
    wire [2:0] camera_eth_awsize;
    wire [1:0] camera_eth_awburst;
    wire [31:0] camera_eth_wstrb;
    wire camera_eth_awvalid, camera_eth_awready, camera_eth_wvalid, camera_eth_wready;
    wire        camera_ddr_ready;
    (* PAP_MARK_DEBUG = "true" *) wire [31:0] camera_ddr_frames;
    (* PAP_MARK_DEBUG = "true" *) wire [31:0] camera_ddr_rejected;
    wire [CTRL_ADDR_WIDTH-1:0] hdmi_awaddr, camera_awaddr;
    wire [3:0] hdmi_awid, camera_awid;
    wire [3:0] hdmi_awlen, camera_awlen;
    wire [2:0] hdmi_awsize, camera_awsize;
    wire [1:0] hdmi_awburst, camera_awburst;
    wire hdmi_awvalid, camera_awvalid;
    wire hdmi_awready, camera_awready;
    wire [255:0] hdmi_wdata, camera_wdata;
    wire [31:0] hdmi_wstrb, camera_wstrb;
    wire hdmi_wvalid, camera_wvalid;
    wire hdmi_wready, camera_wready, hdmi_wlast;
    wire        ddr_upload_rd_cmd_en;
    wire [CTRL_ADDR_WIDTH-1:0] ddr_upload_rd_cmd_addr;
    wire [31:0] ddr_upload_rd_cmd_len;
    wire        ddr_upload_rd_cmd_ready;
    wire        ddr_upload_rd_cmd_done;
    wire        ddr_upload_read_ready;
    wire [MEM_DQ_WIDTH*8-1:0] ddr_upload_read_rdata;
    wire        ddr_upload_read_en;
    wire        ddr_upload_frame_sel;
    wire [CTRL_ADDR_WIDTH-1:0] axi_awaddr;
    wire [3:0]  axi_awid;
    wire [3:0]  axi_awlen;
    wire [2:0]  axi_awsize;
    wire [1:0]  axi_awburst;
    wire        axi_awready;
    wire        axi_awvalid;
    wire [MEM_DQ_WIDTH*8-1:0] axi_wdata;
    wire [MEM_DQ_WIDTH-1:0]   axi_wstrb;
    wire        axi_wlast;
    wire        axi_wvalid;
    wire        axi_wready;
    wire [CTRL_ADDR_WIDTH-1:0] axi_araddr;
    wire [3:0]  axi_arid;
    wire [3:0]  axi_arlen;
    wire [2:0]  axi_arsize;
    wire [1:0]  axi_arburst;
    wire        axi_arvalid;
    wire        axi_arready;
    wire        axi_rready;
    wire [MEM_DQ_WIDTH*8-1:0] axi_rdata;
    wire        axi_rvalid;
    wire        axi_rlast;
    wire [3:0]  axi_rid;
    reg         upload_req_toggle_gray;
    reg         upload_req_toggle_rgb;
    reg         stream_enable;
    reg         stream_req_armed;
    reg         cam_stream_mode;
    reg         udp_raw_stream_mode;
    reg [2:0]   cam_stream_mode_eth;
    (* ASYNC_REG = "TRUE" *) reg [1:0]   cam_transfer_cmd_sys;
    reg [2:0]   udp_raw_stream_mode_eth;
    reg [7:0]   median_hs_d    ;
    reg [2:0]   hdmi_vs_d      ;
    reg [2:0]   hdmi_hs_d      ;
    reg [2:0]   udp_mode_d1    ;
    reg [2:0]   udp_mode_d2    ;
    reg [7:0]   udp_threshold_d1;
    reg [7:0]   udp_threshold_d2;
    reg [2:0]   udp_capture_busy_sys;
    reg [2:0]   ddr_upload_active_pix;

    assign udp_capture_busy   = udp_raw_stream_mode ? udp_capture_busy_rgb : udp_capture_busy_gray;
    assign hdmi_udp_tx_start_en = udp_raw_stream_mode_eth[2] ? udp_tx_start_en_rgb : udp_tx_start_en_gray;
    assign hdmi_udp_tx_data     = udp_raw_stream_mode_eth[2] ? udp_tx_data_rgb     : udp_tx_data_gray;
    assign hdmi_udp_tx_byte_num = udp_raw_stream_mode_eth[2] ? udp_tx_byte_num_rgb : udp_tx_byte_num_gray;
    assign hdmi_udp_tx_done     = cam_stream_mode_eth[2] ? 1'b0       : udp_tx_done;
    assign hdmi_udp_tx_req      = cam_stream_mode_eth[2] ? 1'b0       : udp_tx_req;
    assign cam_udp_tx_done      = cam_stream_mode_eth[2] ? udp_tx_done : 1'b0;
    assign cam_udp_tx_req       = cam_stream_mode_eth[2] ? udp_tx_req  : 1'b0;
    assign udp_tx_start_en      = cam_stream_mode_eth[2] ? udp_tx_start_en_cam : hdmi_udp_tx_start_en;
    assign udp_tx_data          = cam_stream_mode_eth[2] ? udp_tx_data_cam     : hdmi_udp_tx_data;
    assign udp_tx_byte_num      = cam_stream_mode_eth[2] ? udp_tx_byte_num_cam : hdmi_udp_tx_byte_num;
    assign cam_transfer_cmd_sync = cam_transfer_cmd_sys[1];
    assign cam_cfg_rst_n        = rstn_out & cam_pll_locked;

    PLL u_pll (
      .clkin1       (sys_clk   ),   // input//50MHz
      .pll_lock     (locked    ),   // output
      .clkout0      (cfg_clk   )    // output//10MHz
    );

    ms72xx_ctl ms72xx_ctl(
        .clk         (  cfg_clk    ), //input       clk,
        .rst_n       (  rstn_out   ), //input       rstn,
                                
        .init_over   (  init_over  ), //output      init_over,
        .iic_tx_scl  (  iic_tx_scl ), //output      iic_scl,
        .iic_tx_sda  (  iic_tx_sda ), //inout       iic_sda
        .iic_scl     (  iic_scl    ), //output      iic_scl,
        .iic_sda     (  iic_sda    )  //inout       iic_sda
    );

    assign    led_int  =  init_over & ddr_init_done; 

    always @(posedge cfg_clk)
    begin
    	if(!locked)
    	    rstn_1ms <= 16'd0;
    	else
    	begin
    		if(rstn_1ms == 16'h2710)
    		    rstn_1ms <= rstn_1ms;
    		else
    		    rstn_1ms <= rstn_1ms + 1'b1;
    	end
    end
    
    assign rstn_out = (rstn_1ms == 16'h2710);

//HDMI_OUT  =  HDMI_IN 
    assign pixclk_out   =  pixclk_in    ;

    key_mode_switch u_key_mode_switch (
        .clk      (sys_clk          ),
        .rst_n    (rstn_out         ),
        .key_n    (key_mode         ),
        .mode     (preprocess_mode  )
    );

    key_threshold_ctrl u_key_threshold_ctrl (
        .clk       (sys_clk        ),
        .rst_n     (rstn_out       ),
        .key_inc_n (key_th_inc     ),
        .key_dec_n (key_th_dec     ),
        .mode      (preprocess_mode),
        .strength  (lowlight_strength),
        .threshold (bin_threshold  )
    );

    key_pulse_debounce u_key_upload_debounce (
        .clk             (sys_clk          ),
        .rst_n           (rstn_out         ),
        .key_n           (key_upload       ),
        .key_press_pulse (upload_key_pulse )
    );

    cam_pll u_cam_pll (
        .clkin1   (sys_clk        ),
        .clkout0  (               ),
        .clkout1  (               ),
        .clkout2  (clk_25m_cam    ),
        .pll_lock (cam_pll_locked )
    );

    power_on_delay u_power_on_delay(
        .clk_50M      (sys_clk        ),
        .reset_n      (cam_cfg_rst_n  ),
        .camera1_rstn (cam_rst_n      ),
        .camera2_rstn (               ),
        .camera_pwnd  (               ),
        .initial_en   (cam_initial_en )
    );

    reg_config u_reg_config(
        .clk_25M       (clk_25m_cam    ),
        .camera_rstn   (cam_rst_n      ),
        .initial_en    (cam_initial_en ),
        .reg_conf_done (cmos_init_done ),
        .i2c_sclk      (cam_scl        ),
        .i2c_sdat      (cam_sda        ),
        .clock_20k     (clock_20k      ),
        .reg_index     (cam_reg_index  )
    );

    ov5640_capture #(
        .FRAME_WIDTH       (640),
        .FRAME_HEIGHT      (480),
        .VSYNC_ACTIVE_HIGH (CAM_VSYNC_ACTIVE_HIGH),
        .HIGH_BYTE_FIRST   (CAM_HIGH_BYTE_FIRST)
    ) u_ov5640_capture (
        .pclk             (cam_pclk),
        .rst_n            (cam_rst_n & cam_cfg_rst_n),
        .init_done        (cmos_init_done),
        .vsync            (cam_vsync),
        .href             (cam_href),
        .data             (cam_data),
        .configured       (cam_capture_configured),
        .capture_active   (cam_capture_active),
        .frame_start      (cam_frame_start),
        .frame_end        (cam_frame_end),
        .line_start       (cam_line_start),
        .line_end         (cam_line_end),
        .pixel_valid      (cam_pixel_valid),
        .pixel_data       (cam_pixel_data),
        .frame_valid      (cam_frame_valid),
        .frame_error      (cam_frame_error),
        .frame_seen       (cam_frame_seen),
        .last_frame_valid (cam_last_frame_valid),
        .last_error_flags (cam_last_error_flags),
        .good_frames      (cam_good_frames),
        .bad_frames       (cam_bad_frames),
        .last_pixel_count (cam_last_pixel_count),
        .last_line_count  (cam_last_line_count),
        .last_line_bytes  (cam_last_line_bytes),
        .last_pixel_sum   (cam_last_pixel_sum),
        .last_first_pixel (cam_last_first_pixel),
        .last_last_pixel  (cam_last_last_pixel)
    );

    video_source_status #(
        .HDMI_RGB565 (PCIE_RGB565),
        .CAMERA_DMA  (1),
        .ETHERNET_DMA(ETHERNET_VIDEO)
    ) u_video_source_status (
        .pcie_clk             (pcie_frame_dma_clk),
        .pcie_rst_n           (pcie_frame_dma_rst_n),
        .cam_clk              (cam_pclk),
        .cam_rst_n            (cam_rst_n & cam_cfg_rst_n),
        .cam_configured       (cam_capture_configured),
        .cam_active           (cam_capture_active),
        .cam_frame_seen       (cam_frame_seen),
        .cam_last_frame_valid (cam_last_frame_valid),
        .cam_error_flags      (cam_last_error_flags),
        .cam_good_frames      (cam_good_frames),
        .cam_bad_frames       (cam_bad_frames),
        .cam_pixel_count      (cam_last_pixel_count),
        .cam_line_count       (cam_last_line_count),
        .cam_line_bytes       (cam_last_line_bytes),
        .cam_pixel_sum        (cam_last_pixel_sum),
        .cam_first_pixel      (cam_last_first_pixel),
        .cam_last_pixel       (cam_last_last_pixel),
        .ethernet_status      (ethernet_status_pcie),
        .info_data            (pcie_video_info_data)
    );

    start_transfer_ctrl u_start_transfer_ctrl(
        .clk              (eth_rx_clk       ),
        .rst_n            (rstn_out         ),
        .udp_rec_pkt_done (udp_rec_pkt_done ),
        .udp_rec_en       (udp_rec_en       ),
        .udp_rec_data     (udp_rec_data     ),
        .udp_rec_byte_num (udp_rec_byte_num ),
        .transfer_flag    (cam_transfer_cmd )
    );

    always @(posedge sys_clk or negedge rstn_out) begin
        if(!rstn_out) begin
            upload_req_toggle_gray <= 1'b0;
            upload_req_toggle_rgb <= 1'b0;
            stream_enable <= 1'b0;
            stream_req_armed <= 1'b0;
            cam_stream_mode <= 1'b0;
            udp_raw_stream_mode <= 1'b0;
            cam_transfer_cmd_sys <= 2'd0;
            udp_capture_busy_sys <= 3'd0;
        end
        else begin
            cam_transfer_cmd_sys <= {cam_transfer_cmd_sys[0], cam_transfer_cmd};
            udp_capture_busy_sys <= {udp_capture_busy_sys[1:0], udp_capture_busy};
            if(!cam_transfer_cmd_sync)
                cam_stream_mode <= 1'b0;
            else if(!cam_stream_mode && cmos_init_done && !udp_capture_busy_sys[2])
                cam_stream_mode <= 1'b1;

            if(cam_transfer_cmd_sync) begin
                stream_enable <= 1'b0;
                stream_req_armed <= 1'b0;
            end
            else if(upload_key_pulse && !cam_stream_mode) begin
                stream_enable <= ~stream_enable;
                stream_req_armed <= 1'b1;
            end
            else if(!stream_enable) begin
                stream_req_armed <= 1'b0;
            end
            else if(udp_capture_busy_sys[2]) begin
                stream_req_armed <= 1'b1;
            end
            else if(stream_req_armed) begin
                udp_raw_stream_mode <= (preprocess_mode == 3'd0) || (preprocess_mode == 3'd5);
                if((preprocess_mode == 3'd0) || (preprocess_mode == 3'd5))
                    upload_req_toggle_rgb <= ~upload_req_toggle_rgb;
                else
                    upload_req_toggle_gray <= ~upload_req_toggle_gray;
                stream_req_armed <= 1'b0;
            end
        end
    end

    always @(posedge eth_tx_clk or negedge rstn_out) begin
        if(!rstn_out) begin
            cam_stream_mode_eth <= 3'd0;
            udp_raw_stream_mode_eth <= 3'd0;
        end
        else begin
            cam_stream_mode_eth <= {cam_stream_mode_eth[1:0], cam_stream_mode};
            udp_raw_stream_mode_eth <= {udp_raw_stream_mode_eth[1:0], udp_raw_stream_mode};
        end
    end

    hdmi_gray_preprocess u_hdmi_gray_preprocess (
        .clk      (pixclk_in ),
        .rst_n    (init_over ),
        .i_mode   (preprocess_mode),
        .i_threshold(bin_threshold),
        .i_vs     (vs_in     ),
        .i_hs     (hs_in     ),
        .i_de     (de_in     ),
        .i_r      (r_in      ),
        .i_g      (g_in      ),
        .i_b      (b_in      ),
        .o_vs     (gray_vs   ),
        .o_hs     (gray_hs   ),
        .o_de     (gray_de   ),
        .o_r      (gray_r    ),
        .o_g      (gray_g    ),
        .o_b      (gray_b    ),
        .o_y      (gray_y    )
    );

    vip_gray_median_filter u_vip_gray_median_filter (
        .clk              (pixclk_in ),
        .rst_n            (init_over ),
        .pre_frame_vsync  (gray_vs   ),
        .pre_frame_href   (gray_de   ),
        .pre_frame_clken  (gray_de   ),
        .pre_img_y        (gray_y    ),
        .pos_frame_vsync  (median_vs ),
        .pos_frame_href   (median_de ),
        .pos_frame_clken  (           ),
        .pos_img_y        (median_y  )
    );

    always @(posedge pixclk_in or negedge init_over) begin
        if(!init_over)
            median_hs_d <= 8'd0;
        else
            median_hs_d <= {median_hs_d[6:0], gray_hs};
    end

    assign median_hs = median_hs_d[7];

    vip_sobel_edge_detector #(
        .SOBEL_THRESHOLD(128)
    ) u_vip_sobel_edge_detector (
        .clk              (pixclk_in ),
        .rst_n            (init_over ),
        .pre_frame_vsync  (median_vs ),
        .pre_frame_href   (median_de ),
        .pre_frame_clken  (median_de ),
        .pre_img_y        (median_y  ),
        .post_frame_vsync (edge_vs   ),
        .post_frame_href  (edge_hs   ),
        .post_frame_clken (edge_de   ),
        .post_img_bit     (edge_bit  )
    );

    assign edge_data = edge_bit ? 8'hff : 8'h00;

    always @(posedge pixclk_in or negedge init_over) begin
        if(!init_over) begin
            udp_mode_d1      <= 3'd1;
            udp_mode_d2      <= 3'd1;
            udp_threshold_d1 <= 8'd110;
            udp_threshold_d2 <= 8'd110;
        end
        else begin
            udp_mode_d1      <= preprocess_mode;
            udp_mode_d2      <= udp_mode_d1;
            udp_threshold_d1 <= bin_threshold;
            udp_threshold_d2 <= udp_threshold_d1;
        end
    end

    assign udp_bin_data = median_y > udp_threshold_d2;
    assign udp_pix_vs   = (udp_mode_d2 == 3'd1) ? gray_vs   :
                          (udp_mode_d2 == 3'd4) ? edge_vs   : median_vs;
    assign udp_pix_de   = (udp_mode_d2 == 3'd1) ? gray_de   :
                          (udp_mode_d2 == 3'd4) ? edge_de   : median_de;
    assign udp_pix_data = (udp_mode_d2 == 3'd1) ? gray_y    :
                          (udp_mode_d2 == 3'd2) ? median_y  :
                          (udp_mode_d2 == 3'd3) ? (udp_bin_data ? 8'hff : 8'h00) : edge_data;

    assign hdmi_pix_vs    = (preprocess_mode <= 3'd1) ? gray_vs   :
                            (preprocess_mode == 3'd4) ? edge_vs   : median_vs;
    assign hdmi_pix_hs    = (preprocess_mode <= 3'd1) ? gray_hs   :
                            (preprocess_mode == 3'd4) ? edge_hs   : median_hs;
    assign hdmi_pix_de    = (preprocess_mode <= 3'd1) ? gray_de   :
                            (preprocess_mode == 3'd4) ? edge_de   : median_de;
    assign hdmi_bin_data  = median_y > bin_threshold;
    assign hdmi_gray_data = (preprocess_mode <= 3'd1) ? gray_y :
                            (preprocess_mode == 3'd2) ? median_y :
                            (preprocess_mode == 3'd3) ? (hdmi_bin_data ? 8'hff : 8'h00) : edge_data;
    assign hdmi_pix_data  = hdmi_pix_de ? hdmi_gray_data : 8'd0;
    assign hdmi_raw_mode = (preprocess_mode == 3'd0);
    assign hdmi_lowlight_mode = (preprocess_mode == 3'd5);
    hdmi_lowlight_v2 u_hdmi_lowlight (
        .clk(pixclk_in),.rst_n(init_over),.i_strength(lowlight_strength),
        .i_vs(vs_in),.i_hs(hs_in),.i_de(de_in),.i_r(r_in),.i_g(g_in),.i_b(b_in),
        .o_vs(lowlight_vs),.o_hs(lowlight_hs),.o_de(lowlight_de),
        .o_r(lowlight_r_out),.o_g(lowlight_g_out),.o_b(lowlight_b_out)
    );
    assign rgb_pix_vs = hdmi_lowlight_mode ? lowlight_vs : vs_in;
    assign rgb_pix_de = hdmi_lowlight_mode ? lowlight_de : de_in;
    assign rgb_pix_r  = hdmi_lowlight_mode ? lowlight_r_out : r_in;
    assign rgb_pix_g  = hdmi_lowlight_mode ? lowlight_g_out : g_in;
    assign rgb_pix_b  = hdmi_lowlight_mode ? lowlight_b_out : b_in;
    assign ddr_wr_data    = PCIE_RGB565 ? {r_in[7:3], g_in[7:2], b_in[7:3]} :
                                        {8'd0, hdmi_pix_data};
    assign ddr_video_ready = ddr_init_done & ddr_fb_init_done;
    assign ddr_display_ready = ddr_video_ready & ~ddr_upload_active_pix[2];
    assign hdmi_ddr_show_ready = !PCIE_RGB565 & ddr_display_ready & ~hdmi_raw_mode & ~hdmi_lowlight_mode;

    pcie_frame_dma_bridge #(
        .CTRL_ADDR_WIDTH (CTRL_ADDR_WIDTH),
        .RGB565          (PCIE_RGB565),
        .ENABLE_CAMERA   (1),
        .ENABLE_ETHERNET (ETHERNET_VIDEO)
    ) u_pcie_frame_dma_bridge (
        .pcie_clk               (pcie_frame_dma_clk),
        .pcie_rst_n             (pcie_frame_dma_rst_n),
        .bar1_wr_en             (pcie_frame_bar1_wr_en),
        .bar1_wr_addr           (pcie_frame_bar1_wr_addr),
        .bar1_wr_data           (pcie_frame_bar1_wr_data),
        .bar1_wr_byte_en        (pcie_frame_bar1_wr_byte_en),
        .ext_dma_mode           (pcie_frame_dma_mode),
        .ext_mwr64_req          (pcie_frame_mwr64_req),
        .ext_mwr64_req_ack      (pcie_frame_mwr64_req_ack),
        .ext_mwr_busy           (pcie_frame_mwr_busy),
        .ext_req_length         (pcie_frame_req_length),
        .ext_req_addr           (pcie_frame_req_addr),
        .ext_rd_clk_en          (pcie_frame_rd_clk_en),
        .ext_rd_addr            (pcie_frame_rd_addr),
        .ext_rd_data            (pcie_frame_rd_data),
        .status_data            (pcie_frame_status_data),
        .ddr_clk                (ddr_core_clk),
        .ddr_rst_n              (ddr_init_done),
        .upload_active          (ddr_upload_active),
        .upload_source          (ddr_upload_source),
        .upload_rd_cmd_en       (ddr_upload_rd_cmd_en),
        .upload_rd_cmd_addr     (ddr_upload_rd_cmd_addr),
        .upload_rd_cmd_len      (ddr_upload_rd_cmd_len),
        .upload_rd_cmd_ready    (ddr_upload_rd_cmd_ready),
        .upload_rd_cmd_done     (ddr_upload_rd_cmd_done),
        .upload_read_ready      (ddr_upload_read_ready),
        .upload_read_rdata      (ddr_upload_read_rdata),
        .upload_read_en         (ddr_upload_read_en),
        .upload_frame_sel       (ddr_upload_frame_sel),
        .upload_frame_ready     (ddr_upload_frame_ready),
        .camera_frame_ready     (camera_ddr_ready),
        .ethernet_frame_ready   (ethernet_frame_ready),
        .ethernet_frame_base    (ethernet_frame_base),
        .ddr_axi_arvalid        (axi_arvalid),
        .ddr_axi_arready        (axi_arready)
    );

    always @(posedge pixclk_in or negedge init_over) begin
        if(!init_over) begin
            hdmi_vs_d <= 3'd0;
            hdmi_hs_d <= 3'd0;
            ddr_upload_active_pix <= 3'd0;
        end
        else begin
            hdmi_vs_d <= {hdmi_vs_d[1:0], hdmi_pix_vs};
            hdmi_hs_d <= {hdmi_hs_d[1:0], hdmi_pix_hs};
            ddr_upload_active_pix <= {ddr_upload_active_pix[1:0],
                                      ddr_upload_active && !ddr_upload_source};
        end
    end

    fram_buf #(
        .MEM_ROW_WIDTH    (MEM_ROW_WIDTH   ),
        .MEM_COLUMN_WIDTH (MEM_COLUMN_WIDTH),
        .MEM_BANK_WIDTH   (MEM_BANK_WIDTH  ),
        .MEM_DQ_WIDTH     (MEM_DQ_WIDTH    ),
        .H_NUM            (12'd1920        ),
        .V_NUM            (12'd1080        ),
        .PIX_WIDTH        (16              )
    ) u_fram_buf (
        .vin_clk     (pixclk_in        ),
        .wr_fsync    (PCIE_RGB565 ? vs_in : hdmi_pix_vs),
        .wr_en       (PCIE_RGB565 ? de_in : hdmi_pix_de),
        .wr_data     (ddr_wr_data      ),
        .wr_pause    (ddr_upload_active_pix[2]),
        .init_done   (ddr_fb_init_done ),

        .ddr_clk     (ddr_core_clk     ),
        .ddr_rstn    (ddr_init_done    ),

        .vout_clk    (pixclk_in        ),
        .rd_fsync    (PCIE_RGB565 ? vs_in : hdmi_pix_vs),
        .rd_en       (PCIE_RGB565 ? de_in : hdmi_pix_de),
        .vout_de     (ddr_vout_de      ),
        .vout_data   (ddr_vout_data    ),

        .upload_active       (ddr_upload_active       ),
        .upload_rd_cmd_en    (ddr_upload_rd_cmd_en    ),
        .upload_rd_cmd_addr  (ddr_upload_rd_cmd_addr  ),
        .upload_rd_cmd_len   (ddr_upload_rd_cmd_len   ),
        .upload_rd_cmd_ready (ddr_upload_rd_cmd_ready ),
        .upload_rd_cmd_done  (ddr_upload_rd_cmd_done  ),
        .upload_read_ready   (ddr_upload_read_ready   ),
        .upload_read_rdata   (ddr_upload_read_rdata   ),
        .upload_read_en      (ddr_upload_read_en      ),
        .upload_frame_sel    (ddr_upload_frame_sel    ),
        .upload_frame_ready  (ddr_upload_frame_ready  ),

        .axi_awaddr  (hdmi_awaddr      ),
        .axi_awid    (hdmi_awid        ),
        .axi_awlen   (hdmi_awlen       ),
        .axi_awsize  (hdmi_awsize      ),
        .axi_awburst (hdmi_awburst     ),
        .axi_awready (hdmi_awready     ),
        .axi_awvalid (hdmi_awvalid     ),

        .axi_wdata   (hdmi_wdata       ),
        .axi_wstrb   (hdmi_wstrb       ),
        .axi_wlast   (hdmi_wlast       ),
        .axi_wvalid  (hdmi_wvalid      ),
        .axi_wready  (hdmi_wready      ),
        .axi_bid     (4'd0             ),

        .axi_araddr  (axi_araddr       ),
        .axi_arid    (axi_arid         ),
        .axi_arlen   (axi_arlen        ),
        .axi_arsize  (axi_arsize       ),
        .axi_arburst (axi_arburst      ),
        .axi_arvalid (axi_arvalid      ),
        .axi_arready (axi_arready      ),

        .axi_rready  (axi_rready       ),
        .axi_rdata   (axi_rdata        ),
        .axi_rvalid  (axi_rvalid       ),
        .axi_rlast   (axi_rlast        ),
        .axi_rid     (axi_rid          )
    );

    camera_ddr_snapshot #(.ADDR_WIDTH(CTRL_ADDR_WIDTH),
                          .PREFETCH(CAMERA_DDR_PREFETCH)) u_camera_ddr_snapshot (
        .rst_n(ddr_init_done & cam_rst_n & cam_cfg_rst_n),
        .cam_clk(cam_pclk), .frame_start(cam_frame_start),
        .frame_end(cam_frame_end), .frame_valid(cam_frame_valid),
        .pixel_valid(cam_pixel_valid), .pixel_data(cam_pixel_data),
        .ddr_clk(ddr_core_clk),
        .capture_request(ddr_upload_active && ddr_upload_source == 1),
        .frame_ready(camera_ddr_ready), .frames_captured(camera_ddr_frames),
        .frames_rejected(camera_ddr_rejected),
        .axi_awaddr(camera_awaddr), .axi_awid(camera_awid),
        .axi_awlen(camera_awlen), .axi_awsize(camera_awsize),
        .axi_awburst(camera_awburst), .axi_awvalid(camera_awvalid),
        .axi_awready(camera_awready), .axi_wdata(camera_wdata),
        .axi_wstrb(camera_wstrb), .axi_wvalid(camera_wvalid),
        .axi_wready(camera_wready)
    );

    generate if (ETHERNET_VIDEO) begin : g_ethernet_video
        ethernet_ddr_video #(.ADDR_WIDTH(CTRL_ADDR_WIDTH)) u_ethernet_video (
            .rst_n(ddr_init_done), .rx_clk(eth_rx_clk),
            .rx_en(udp_rec_en), .rx_data(udp_rec_data),
            .rx_done(udp_rec_pkt_done), .rx_bytes(udp_rec_byte_num),
            .ddr_clk(ddr_core_clk),
            .capture_request(ddr_upload_active && ddr_upload_source == 2),
            .frame_ready(ethernet_frame_ready), .read_base(ethernet_frame_base),
            .status(ethernet_status), .axi_awaddr(ethernet_awaddr),
            .axi_awvalid(ethernet_awvalid), .axi_awready(ethernet_awready),
            .axi_wdata(ethernet_wdata), .axi_wvalid(ethernet_wvalid),
            .axi_wready(ethernet_wready)
        );
    end else begin : g_no_ethernet_video
        assign ethernet_frame_ready = 0;
        assign ethernet_frame_base = 0;
        assign ethernet_status = 0;
        assign ethernet_awaddr = 0;
        assign ethernet_awvalid = 0;
        assign ethernet_wdata = 0;
        assign ethernet_wvalid = 0;
    end endgenerate

    video_status_snapshot u_ethernet_status_snapshot (
        .source_clk(ddr_core_clk), .source_rst_n(ddr_init_done),
        .source_data(ethernet_status), .destination_clk(pcie_frame_dma_clk),
        .destination_rst_n(pcie_frame_dma_rst_n), .destination_data(ethernet_status_pcie)
    );

    ddr_write_arbiter #(.ADDR_WIDTH(CTRL_ADDR_WIDTH)) u_camera_eth_arbiter (
        .clk(ddr_core_clk), .rst_n(ddr_init_done),
        .hdmi_awaddr(camera_awaddr), .hdmi_awid(camera_awid),
        .hdmi_awlen(camera_awlen), .hdmi_awsize(camera_awsize),
        .hdmi_awburst(camera_awburst), .hdmi_awvalid(camera_awvalid),
        .hdmi_awready(camera_awready), .hdmi_wdata(camera_wdata),
        .hdmi_wstrb(camera_wstrb), .hdmi_wvalid(camera_wvalid),
        .hdmi_wready(camera_wready), .hdmi_wlast(),
        .cam_awaddr(ethernet_awaddr), .cam_awid(4'd2),
        .cam_awlen(4'd0), .cam_awsize(3'b110), .cam_awburst(2'b01),
        .cam_awvalid(ethernet_awvalid), .cam_awready(ethernet_awready),
        .cam_wdata(ethernet_wdata), .cam_wstrb(32'hffffffff),
        .cam_wvalid(ethernet_wvalid), .cam_wready(ethernet_wready),
        .axi_awaddr(camera_eth_awaddr), .axi_awid(camera_eth_awid),
        .axi_awlen(camera_eth_awlen), .axi_awsize(camera_eth_awsize),
        .axi_awburst(camera_eth_awburst), .axi_awvalid(camera_eth_awvalid),
        .axi_awready(camera_eth_awready), .axi_wdata(camera_eth_wdata),
        .axi_wstrb(camera_eth_wstrb), .axi_wvalid(camera_eth_wvalid),
        .axi_wready(camera_eth_wready), .axi_wlast(1'b1)
    );

    ddr_write_arbiter #(.ADDR_WIDTH(CTRL_ADDR_WIDTH)) u_ddr_write_arbiter (
        .clk(ddr_core_clk), .rst_n(ddr_init_done),
        .hdmi_awaddr(hdmi_awaddr), .hdmi_awid(hdmi_awid),
        .hdmi_awlen(hdmi_awlen), .hdmi_awsize(hdmi_awsize),
        .hdmi_awburst(hdmi_awburst), .hdmi_awvalid(hdmi_awvalid),
        .hdmi_awready(hdmi_awready), .hdmi_wdata(hdmi_wdata),
        .hdmi_wstrb(hdmi_wstrb), .hdmi_wvalid(hdmi_wvalid),
        .hdmi_wready(hdmi_wready), .hdmi_wlast(hdmi_wlast),
        .cam_awaddr(camera_eth_awaddr), .cam_awid(camera_eth_awid),
        .cam_awlen(camera_eth_awlen), .cam_awsize(camera_eth_awsize),
        .cam_awburst(camera_eth_awburst), .cam_awvalid(camera_eth_awvalid),
        .cam_awready(camera_eth_awready), .cam_wdata(camera_eth_wdata),
        .cam_wstrb(camera_eth_wstrb), .cam_wvalid(camera_eth_wvalid),
        .cam_wready(camera_eth_wready), .axi_awaddr(axi_awaddr),
        .axi_awid(axi_awid), .axi_awlen(axi_awlen),
        .axi_awsize(axi_awsize), .axi_awburst(axi_awburst),
        .axi_awvalid(axi_awvalid), .axi_awready(axi_awready),
        .axi_wdata(axi_wdata), .axi_wstrb(axi_wstrb),
        .axi_wvalid(axi_wvalid), .axi_wready(axi_wready), .axi_wlast(axi_wlast)
    );

    DDR3_50H u_DDR3_50H (
        .ref_clk                  (sys_clk        ),
        .resetn                   (rstn_out       ),
        .ddr_init_done            (ddr_init_done  ),
        .ddrphy_clkin             (ddr_core_clk   ),
        .pll_lock                 (ddr_pll_lock   ),

        .axi_awaddr               (axi_awaddr     ),
        .axi_awuser_ap            (1'b0           ),
        .axi_awuser_id            (axi_awid       ),
        .axi_awlen                (axi_awlen      ),
        .axi_awready              (axi_awready    ),
        .axi_awvalid              (axi_awvalid    ),
        .axi_wdata                (axi_wdata      ),
        .axi_wstrb                (axi_wstrb      ),
        .axi_wready               (axi_wready     ),
        .axi_wusero_id            (               ),
        .axi_wusero_last          (axi_wlast      ),
        .axi_araddr               (axi_araddr     ),
        .axi_aruser_ap            (1'b0           ),
        .axi_aruser_id            (axi_arid       ),
        .axi_arlen                (axi_arlen      ),
        .axi_arready              (axi_arready    ),
        .axi_arvalid              (axi_arvalid    ),
        .axi_rdata                (axi_rdata      ),
        .axi_rid                  (axi_rid        ),
        .axi_rlast                (axi_rlast      ),
        .axi_rvalid               (axi_rvalid     ),

        .apb_clk                  (1'b0           ),
        .apb_rst_n                (1'b1           ),
        .apb_sel                  (1'b0           ),
        .apb_enable               (1'b0           ),
        .apb_addr                 (8'd0           ),
        .apb_write                (1'b0           ),
        .apb_ready                (               ),
        .apb_wdata                (16'd0          ),
        .apb_rdata                (               ),
        .apb_int                  (               ),

        .mem_rst_n                (mem_rst_n      ),
        .mem_ck                   (mem_ck         ),
        .mem_ck_n                 (mem_ck_n       ),
        .mem_cke                  (mem_cke        ),
        .mem_cs_n                 (mem_cs_n       ),
        .mem_ras_n                (mem_ras_n      ),
        .mem_cas_n                (mem_cas_n      ),
        .mem_we_n                 (mem_we_n       ),
        .mem_odt                  (mem_odt        ),
        .mem_a                    (mem_a          ),
        .mem_ba                   (mem_ba         ),
        .mem_dqs                  (mem_dqs        ),
        .mem_dqs_n                (mem_dqs_n      ),
        .mem_dq                   (mem_dq         ),
        .mem_dm                   (mem_dm         ),

        .debug_data               (               ),
        .debug_slice_state        (               ),
        .debug_calib_ctrl         (               ),
        .ck_dly_set_bin           (               ),
        .force_ck_dly_en          (1'b0           ),
        .force_ck_dly_set_bin     (8'h05          ),
        .dll_step                 (               ),
        .dll_lock                 (               ),
        .init_read_clk_ctrl       (2'b0           ),
        .init_slip_step           (4'b0           ),
        .force_read_clk_ctrl      (1'b0           ),
        .ddrphy_gate_update_en    (1'b0           ),
        .update_com_val_err_flag  (               ),
        .rd_fake_stop             (1'b0           )
    );

    udp_test_pkt #(
        .OUT_WIDTH     (640 ),
        .OUT_HEIGHT    (480 ),
        .X_START_SKIP  (0   ),
        .Y_START_SKIP  (60  ),
        .X_SAMPLE_STEP (3   ),
        .Y_SAMPLE_STEP (2   ),
        .SEG_PIXELS    (640 ),
        .INTER_PKT_GAP (128 )
    ) u_udp_video_gray_pkt (
        .capture_req_toggle(upload_req_toggle_gray),
        .pix_clk           (pixclk_in        ),
        .pix_rst_n         (init_over & ~cam_stream_mode),
        .pix_vs            (udp_pix_vs       ),
        .pix_de            (udp_pix_de       ),
        .pix_data          (udp_pix_data     ),
        .eth_clk           (eth_tx_clk       ),
        .eth_rst_n         (rstn_out & ~cam_stream_mode_eth[2]),
        .udp_tx_done       (hdmi_udp_tx_done ),
        .udp_tx_req        (hdmi_udp_tx_req  ),
        .udp_tx_start_en   (udp_tx_start_en_gray),
        .udp_tx_data       (udp_tx_data_gray    ),
        .udp_tx_byte_num   (udp_tx_byte_num_gray),
        .capture_busy      (udp_capture_busy_gray)
    );

    udp_rgb_test_pkt #(
        .OUT_WIDTH     (640 ),
        .OUT_HEIGHT    (480 ),
        .X_START_SKIP  (0   ),
        .Y_START_SKIP  (60  ),
        .X_SAMPLE_STEP (3   ),
        .Y_SAMPLE_STEP (2   ),
        .SEG_PIXELS    (480 ),
        .INTER_PKT_GAP (128 )
    ) u_udp_video_rgb_pkt (
        .capture_req_toggle(upload_req_toggle_rgb),
        .pix_clk           (pixclk_in       ),
        .pix_rst_n         (init_over & ~cam_stream_mode),
        .pix_vs            (rgb_pix_vs      ),
        .pix_de            (rgb_pix_de      ),
        .pix_r             (rgb_pix_r       ),
        .pix_g             (rgb_pix_g       ),
        .pix_b             (rgb_pix_b       ),
        .eth_clk           (eth_tx_clk      ),
        .eth_rst_n         (rstn_out & ~cam_stream_mode_eth[2]),
        .udp_tx_done       (hdmi_udp_tx_done),
        .udp_tx_req        (hdmi_udp_tx_req ),
        .udp_tx_start_en   (udp_tx_start_en_rgb),
        .udp_tx_data       (udp_tx_data_rgb    ),
        .udp_tx_byte_num   (udp_tx_byte_num_rgb),
        .capture_busy      (udp_capture_busy_rgb)
    );

    // 摄像头模式独占网络口，避免与 HDMI 抓图上传同时抢占同一套 PHY/UDP 发送链路。
    img_data_pkt u_cam_img_data_pkt(
        .rst_n           (rstn_out           ),
        .cam_pclk        (cam_pclk           ),
        .img_vsync       (cam_vsync          ),
        .img_data_en     (cam_href           ),
        .img_data        (cam_data           ),
        .transfer_flag   (cam_stream_mode    ),
        .eth_tx_clk      (eth_tx_clk         ),
        .udp_tx_req      (cam_udp_tx_req     ),
        .udp_tx_done     (cam_udp_tx_done    ),
        .udp_tx_start_en (udp_tx_start_en_cam),
        .udp_tx_data     (udp_tx_data_cam    ),
        .udp_tx_byte_num (udp_tx_byte_num_cam)
    );

    eth_top #(
        .BOARD_MAC (BOARD_MAC),
        .BOARD_IP  (BOARD_IP ),
        .DES_MAC   (DES_MAC  ),
        .DES_IP    (DES_IP   )
    ) u_eth_top (
        .sys_rst_n        (rstn_out        ),
        .eth_rxc          (eth_rxc         ),
        .eth_rx_ctl       (eth_rx_ctl      ),
        .eth_rxd          (eth_rxd         ),
        .eth_txc          (eth_txc         ),
        .eth_tx_ctl       (eth_tx_ctl      ),
        .eth_txd          (eth_txd         ),
        .gmii_tx_clk      (eth_tx_clk      ),
        .udp_tx_start_en  (udp_tx_start_en ),
        .tx_data          (udp_tx_data     ),
        .tx_byte_num      (udp_tx_byte_num ),
        .udp_tx_done      (udp_tx_done     ),
        .tx_req           (udp_tx_req      ),
        .gmii_rx_clk      (eth_rx_clk      ),
        .rec_pkt_done     (udp_rec_pkt_done),
        .rec_en           (udp_rec_en      ),
        .rec_data         (udp_rec_data    ),
        .rec_byte_num     (udp_rec_byte_num)
    );

    always  @(posedge pixclk_out)begin
        if(!init_over)begin
    	        vs_out       <=  1'b0        ;
            hs_out       <=  1'b0        ;
            de_out       <=  1'b0        ;
            r_out        <=  8'b0        ;
            g_out        <=  8'b0        ;
            b_out        <=  8'b0        ;
    	end
    	    else begin
            vs_out       <=  (hdmi_raw_mode || hdmi_lowlight_mode) ? rgb_pix_vs :
                             hdmi_ddr_show_ready ? hdmi_vs_d[2]       : hdmi_pix_vs;
            hs_out       <=  (hdmi_raw_mode || hdmi_lowlight_mode) ? (hdmi_lowlight_mode ? lowlight_hs : hs_in) :
                             hdmi_ddr_show_ready ? hdmi_hs_d[2]       : hdmi_pix_hs;
            de_out       <=  (hdmi_raw_mode || hdmi_lowlight_mode) ? rgb_pix_de :
                             hdmi_ddr_show_ready ? ddr_vout_de        : hdmi_pix_de;
            r_out        <=  (hdmi_raw_mode || hdmi_lowlight_mode) ? (rgb_pix_de ? rgb_pix_r : 8'd0) :
                             hdmi_ddr_show_ready ? ddr_vout_data[7:0] : hdmi_pix_data;
            g_out        <=  (hdmi_raw_mode || hdmi_lowlight_mode) ? (rgb_pix_de ? rgb_pix_g : 8'd0) :
                             hdmi_ddr_show_ready ? ddr_vout_data[7:0] : hdmi_pix_data;
            b_out        <=  (hdmi_raw_mode || hdmi_lowlight_mode) ? (rgb_pix_de ? rgb_pix_b : 8'd0) :
                             hdmi_ddr_show_ready ? ddr_vout_data[7:0] : hdmi_pix_data;
        end
    end

endmodule

// Read-only BAR0 0x170: magic, atomic event word, pressed mask, ABI version.
// Keys are synchronized and debounced directly in the PCIe clock domain;
// no multi-bit event counter crosses a clock boundary.
module junction_key_input #(
    parameter DEBOUNCE_CYCLES = 2500000 // 20ms at 125MHz, 40ms at 62.5MHz.
)(
    input wire clk,
    input wire rst_n,
    input wire [2:0] keys_n,
    output wire [127:0] status
);
    (* ASYNC_REG = "TRUE" *) reg [2:0] key_meta, key_sync;
    reg [2:0] candidate, stable;
    reg [31:0] count;
    reg armed;
    reg [29:0] event_sequence;
    reg [1:0] direction;
    assign status = {32'd1, 29'd0, ~stable, event_sequence, direction, 32'h4a4b3031};
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            key_meta <= 3'b111; key_sync <= 3'b111;
            candidate <= 3'b111; stable <= 3'b111;
            count <= 0; armed <= 0; event_sequence <= 0; direction <= 0;
        end else begin
            key_meta <= keys_n; key_sync <= key_meta;
            if (candidate != key_sync) begin
                candidate <= key_sync; count <= 0;
            end else if (count < DEBOUNCE_CYCLES) begin
                count <= count + 1'b1;
                if (count == DEBOUNCE_CYCLES-1) begin
                    stable <= candidate;
                    if (candidate == 3'b111) armed <= 1;
                    else begin
                        armed <= 0;
                        // Chords and held-at-boot inputs never select a route.
                        if (armed && (candidate == 3'b110 || candidate == 3'b101 || candidate == 3'b011)) begin
                            event_sequence <= event_sequence == 30'h3fffffff ? 30'd1 : event_sequence + 1'b1;
                            case (candidate)
                                3'b110: direction <= 1; // KEY5: left
                                3'b101: direction <= 2; // KEY6: straight
                                3'b011: direction <= 3; // KEY7: right
                                default: direction <= 0;
                            endcase
                        end
                    end
                end
            end
        end
    end
endmodule

module key_pulse_debounce #(
    parameter DEBOUNCE_CNT_MAX = 20'd999999
)(
    input  wire clk,
    input  wire rst_n,
    input  wire key_n,
    output reg  key_press_pulse
);

    reg        key_sync0;
    reg        key_sync1;
    reg        key_state;
    reg [19:0] debounce_cnt;

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            key_sync0 <= 1'b1;
            key_sync1 <= 1'b1;
        end
        else begin
            key_sync0 <= key_n;
            key_sync1 <= key_sync0;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            key_state       <= 1'b1;
            debounce_cnt    <= 20'd0;
            key_press_pulse <= 1'b0;
        end
        else begin
            key_press_pulse <= 1'b0;
            if(key_sync1 != key_state) begin
                if(debounce_cnt >= DEBOUNCE_CNT_MAX) begin
                    debounce_cnt <= 20'd0;
                    key_state    <= key_sync1;
                    if(key_sync1 == 1'b0)
                        key_press_pulse <= 1'b1;
                end
                else begin
                    debounce_cnt <= debounce_cnt + 1'b1;
                end
            end
            else begin
                debounce_cnt <= 20'd0;
            end
        end
    end

endmodule

module key_threshold_ctrl #(
    parameter THRESHOLD_DEFAULT = 8'd110,
    parameter THRESHOLD_STEP    = 8'd4,
    parameter THRESHOLD_MIN     = 8'd16,
    parameter THRESHOLD_MAX     = 8'd240
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       key_inc_n,
    input  wire       key_dec_n,
    input  wire [2:0] mode,
    output reg  [1:0] strength,
    output reg  [7:0] threshold
);

    wire inc_pulse;
    wire dec_pulse;

    key_pulse_debounce u_key_inc_debounce (
        .clk             (clk       ),
        .rst_n           (rst_n     ),
        .key_n           (key_inc_n ),
        .key_press_pulse (inc_pulse )
    );

    key_pulse_debounce u_key_dec_debounce (
        .clk             (clk       ),
        .rst_n           (rst_n     ),
        .key_n           (key_dec_n ),
        .key_press_pulse (dec_pulse )
    );

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            threshold <= THRESHOLD_DEFAULT;
            strength <= 2'd1;
        end
        else if(mode == 3'd5) begin
            if(inc_pulse && !dec_pulse && strength<2) strength<=strength+1'b1;
            else if(dec_pulse && !inc_pulse && strength>0) strength<=strength-1'b1;
        end
        else if(inc_pulse && !dec_pulse) begin
            if(threshold >= THRESHOLD_MAX - THRESHOLD_STEP)
                threshold <= THRESHOLD_MAX;
            else
                threshold <= threshold + THRESHOLD_STEP;
        end
        else if(dec_pulse && !inc_pulse) begin
            if(threshold <= THRESHOLD_MIN + THRESHOLD_STEP)
                threshold <= THRESHOLD_MIN;
            else
                threshold <= threshold - THRESHOLD_STEP;
        end
    end

endmodule

module key_mode_switch #(
    parameter DEBOUNCE_CNT_MAX = 20'd999999
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       key_n,
    output reg  [2:0] mode
);

    reg        key_sync0;
    reg        key_sync1;
    reg        key_state;
    reg [19:0] debounce_cnt;

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            key_sync0 <= 1'b1;
            key_sync1 <= 1'b1;
        end
        else begin
            key_sync0 <= key_n;
            key_sync1 <= key_sync0;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            key_state    <= 1'b1;
            debounce_cnt <= 20'd0;
            mode         <= 3'd1;
        end
        else if(key_sync1 != key_state) begin
            if(debounce_cnt >= DEBOUNCE_CNT_MAX) begin
                debounce_cnt <= 20'd0;
                key_state    <= key_sync1;
                if(key_sync1 == 1'b0) begin
                    if(mode >= 3'd5)
                        mode <= 3'd0;
                    else
                        mode <= mode + 3'd1;
                end
            end
            else begin
                debounce_cnt <= debounce_cnt + 1'b1;
            end
        end
        else begin
            debounce_cnt <= 20'd0;
        end
    end

endmodule

module hdmi_gray_preprocess (
    input  wire [2:0]  i_mode,
    input  wire [7:0]  i_threshold,
    input  wire        clk,
    input  wire        rst_n,

    input  wire        i_vs,
    input  wire        i_hs,
    input  wire        i_de,
    input  wire [7:0]  i_r,
    input  wire [7:0]  i_g,
    input  wire [7:0]  i_b,

    output wire        o_vs,
    output wire        o_hs,
    output wire        o_de,
    output wire [7:0]  o_r,
    output wire [7:0]  o_g,
    output wire [7:0]  o_b,
    output wire [7:0]  o_y
);

    reg [15:0] r_mult;
    reg [15:0] g_mult;
    reg [15:0] b_mult;
    reg [15:0] y_sum;
    reg [7:0]  y_data;
    reg [7:0]  y_d4;
    reg [3:0]  vs_d;
    reg [3:0]  hs_d;
    reg [3:0]  de_d;
    reg [7:0]  r_d1;
    reg [7:0]  r_d2;
    reg [7:0]  r_d3;
    reg [7:0]  r_d4;
    reg [7:0]  g_d1;
    reg [7:0]  g_d2;
    reg [7:0]  g_d3;
    reg [7:0]  g_d4;
    reg [7:0]  b_d1;
    reg [7:0]  b_d2;
    reg [7:0]  b_d3;
    reg [7:0]  b_d4;
    reg [2:0]  mode_d1;
    reg [2:0]  mode_d2;
    reg [7:0]  threshold_d1;
    reg [7:0]  threshold_d2;

    wire        bin_data;
    wire [7:0]  bin_rgb;
    wire [7:0]  inv_bin_rgb;

    assign o_vs = vs_d[3];
    assign o_hs = hs_d[3];
    assign o_de = de_d[3];
    assign bin_data    = y_d4 > threshold_d2;
    assign bin_rgb     = bin_data ? 8'hff : 8'h00;
    assign inv_bin_rgb = bin_data ? 8'h00 : 8'hff;
    assign o_r  = !de_d[3]       ? 8'd0        :
                  (mode_d2 == 3'd0) ? r_d4     :
                  (mode_d2 == 3'd1) ? y_d4     :
                  (mode_d2 == 3'd2) ? bin_rgb  :
                  (mode_d2 == 3'd5) ? r_d4 : inv_bin_rgb;
    assign o_g  = !de_d[3]       ? 8'd0        :
                  (mode_d2 == 3'd0) ? g_d4     :
                  (mode_d2 == 3'd1) ? y_d4     :
                  (mode_d2 == 3'd2) ? bin_rgb  :
                  (mode_d2 == 3'd5) ? g_d4 : inv_bin_rgb;
    assign o_b  = !de_d[3]       ? 8'd0        :
                  (mode_d2 == 3'd0) ? b_d4     :
                  (mode_d2 == 3'd1) ? y_d4     :
                  (mode_d2 == 3'd2) ? bin_rgb  :
                  (mode_d2 == 3'd5) ? b_d4 : inv_bin_rgb;
    assign o_y  = !de_d[3] ? 8'd0 : y_d4;

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            r_mult <= 16'd0;
            g_mult <= 16'd0;
            b_mult <= 16'd0;
        end
        else begin
            r_mult <= i_r * 8'd77;
            g_mult <= i_g * 8'd150;
            b_mult <= i_b * 8'd29;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)
            y_sum <= 16'd0;
        else
            y_sum <= r_mult + g_mult + b_mult;
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)
            y_data <= 8'd0;
        else
            y_data <= y_sum[15:8];
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)
            y_d4 <= 8'd0;
        else
            y_d4 <= y_data;
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            vs_d <= 4'd0;
            hs_d <= 4'd0;
            de_d <= 4'd0;
        end
        else begin
            vs_d <= {vs_d[2:0], i_vs};
            hs_d <= {hs_d[2:0], i_hs};
            de_d <= {de_d[2:0], i_de};
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            r_d1 <= 8'd0;
            r_d2 <= 8'd0;
            r_d3 <= 8'd0;
            r_d4 <= 8'd0;
            g_d1 <= 8'd0;
            g_d2 <= 8'd0;
            g_d3 <= 8'd0;
            g_d4 <= 8'd0;
            b_d1 <= 8'd0;
            b_d2 <= 8'd0;
            b_d3 <= 8'd0;
            b_d4 <= 8'd0;
        end
        else begin
            r_d1 <= i_r;
            r_d2 <= r_d1;
            r_d3 <= r_d2;
            r_d4 <= r_d3;
            g_d1 <= i_g;
            g_d2 <= g_d1;
            g_d3 <= g_d2;
            g_d4 <= g_d3;
            b_d1 <= i_b;
            b_d2 <= b_d1;
            b_d3 <= b_d2;
            b_d4 <= b_d3;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            mode_d1 <= 3'd1;
            mode_d2 <= 3'd1;
            threshold_d1 <= 8'd110;
            threshold_d2 <= 8'd110;
        end
        else begin
            mode_d1 <= i_mode;
            mode_d2 <= mode_d1;
            threshold_d1 <= i_threshold;
            threshold_d2 <= threshold_d1;
        end
    end

endmodule
