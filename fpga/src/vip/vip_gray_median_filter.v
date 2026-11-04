module vip_gray_median_filter(
    input        clk,
    input        rst_n,

    input        pre_frame_vsync,
    input        pre_frame_href,
    input        pre_frame_clken,
    input  [7:0] pre_img_y,

    output       pos_frame_vsync,
    output       pos_frame_href,
    output       pos_frame_clken,
    output [7:0] pos_img_y
);

    wire        matrix_frame_vsync;
    wire        matrix_frame_href;
    wire        matrix_frame_clken;
    wire [7:0]  matrix_p11;
    wire [7:0]  matrix_p12;
    wire [7:0]  matrix_p13;
    wire [7:0]  matrix_p21;
    wire [7:0]  matrix_p22;
    wire [7:0]  matrix_p23;
    wire [7:0]  matrix_p31;
    wire [7:0]  matrix_p32;
    wire [7:0]  matrix_p33;
    wire [7:0]  mid_value;

    assign pos_img_y = pos_frame_href ? mid_value : 8'd0;

    vip_matrix_generate_3x3_8bit u_vip_matrix_generate_3x3_8bit (
        .clk                 (clk                ),
        .rst_n               (rst_n              ),
        .pre_frame_vsync     (pre_frame_vsync    ),
        .pre_frame_href      (pre_frame_href     ),
        .pre_frame_clken     (pre_frame_clken    ),
        .pre_img_y           (pre_img_y          ),
        .matrix_frame_vsync  (matrix_frame_vsync ),
        .matrix_frame_href   (matrix_frame_href  ),
        .matrix_frame_clken  (matrix_frame_clken ),
        .matrix_p11          (matrix_p11         ),
        .matrix_p12          (matrix_p12         ),
        .matrix_p13          (matrix_p13         ),
        .matrix_p21          (matrix_p21         ),
        .matrix_p22          (matrix_p22         ),
        .matrix_p23          (matrix_p23         ),
        .matrix_p31          (matrix_p31         ),
        .matrix_p32          (matrix_p32         ),
        .matrix_p33          (matrix_p33         )
    );

    median_filter_3x3 u_median_filter_3x3 (
        .clk                (clk                ),
        .rst_n              (rst_n              ),
        .median_frame_vsync (matrix_frame_vsync ),
        .median_frame_href  (matrix_frame_href  ),
        .median_frame_clken (matrix_frame_clken ),
        .data11             (matrix_p11         ),
        .data12             (matrix_p12         ),
        .data13             (matrix_p13         ),
        .data21             (matrix_p21         ),
        .data22             (matrix_p22         ),
        .data23             (matrix_p23         ),
        .data31             (matrix_p31         ),
        .data32             (matrix_p32         ),
        .data33             (matrix_p33         ),
        .target_data        (mid_value          ),
        .pos_median_vsync   (pos_frame_vsync    ),
        .pos_median_href    (pos_frame_href     ),
        .pos_median_clken   (pos_frame_clken    )
    );

endmodule
