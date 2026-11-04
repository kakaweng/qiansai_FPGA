module median_filter_3x3(
    input        clk,
    input        rst_n,
    input        median_frame_vsync,
    input        median_frame_href,
    input        median_frame_clken,

    input  [7:0] data11,
    input  [7:0] data12,
    input  [7:0] data13,
    input  [7:0] data21,
    input  [7:0] data22,
    input  [7:0] data23,
    input  [7:0] data31,
    input  [7:0] data32,
    input  [7:0] data33,

    output [7:0] target_data,
    output       pos_median_vsync,
    output       pos_median_href,
    output       pos_median_clken
);

    reg [2:0] median_frame_vsync_r;
    reg [2:0] median_frame_href_r;
    reg [2:0] median_frame_clken_r;

    wire [7:0] max_data1;
    wire [7:0] mid_data1;
    wire [7:0] min_data1;
    wire [7:0] max_data2;
    wire [7:0] mid_data2;
    wire [7:0] min_data2;
    wire [7:0] max_data3;
    wire [7:0] mid_data3;
    wire [7:0] min_data3;
    wire [7:0] max_min_data;
    wire [7:0] mid_mid_data;
    wire [7:0] min_max_data;

    assign pos_median_vsync = median_frame_vsync_r[2];
    assign pos_median_href  = median_frame_href_r[2];
    assign pos_median_clken = median_frame_clken_r[2];

    sort3 u_sort3_1 (
        .clk      (clk       ),
        .rst_n    (rst_n     ),
        .data1    (data11    ),
        .data2    (data12    ),
        .data3    (data13    ),
        .max_data (max_data1 ),
        .mid_data (mid_data1 ),
        .min_data (min_data1 )
    );

    sort3 u_sort3_2 (
        .clk      (clk       ),
        .rst_n    (rst_n     ),
        .data1    (data21    ),
        .data2    (data22    ),
        .data3    (data23    ),
        .max_data (max_data2 ),
        .mid_data (mid_data2 ),
        .min_data (min_data2 )
    );

    sort3 u_sort3_3 (
        .clk      (clk       ),
        .rst_n    (rst_n     ),
        .data1    (data31    ),
        .data2    (data32    ),
        .data3    (data33    ),
        .max_data (max_data3 ),
        .mid_data (mid_data3 ),
        .min_data (min_data3 )
    );

    sort3 u_sort3_4 (
        .clk      (clk          ),
        .rst_n    (rst_n        ),
        .data1    (max_data1    ),
        .data2    (max_data2    ),
        .data3    (max_data3    ),
        .max_data (             ),
        .mid_data (             ),
        .min_data (max_min_data )
    );

    sort3 u_sort3_5 (
        .clk      (clk          ),
        .rst_n    (rst_n        ),
        .data1    (mid_data1    ),
        .data2    (mid_data2    ),
        .data3    (mid_data3    ),
        .max_data (             ),
        .mid_data (mid_mid_data ),
        .min_data (             )
    );

    sort3 u_sort3_6 (
        .clk      (clk          ),
        .rst_n    (rst_n        ),
        .data1    (min_data1    ),
        .data2    (min_data2    ),
        .data3    (min_data3    ),
        .max_data (min_max_data ),
        .mid_data (             ),
        .min_data (             )
    );

    sort3 u_sort3_7 (
        .clk      (clk          ),
        .rst_n    (rst_n        ),
        .data1    (max_min_data ),
        .data2    (mid_mid_data ),
        .data3    (min_max_data ),
        .max_data (             ),
        .mid_data (target_data  ),
        .min_data (             )
    );

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            median_frame_vsync_r <= 3'd0;
            median_frame_href_r  <= 3'd0;
            median_frame_clken_r <= 3'd0;
        end
        else begin
            median_frame_vsync_r <= {median_frame_vsync_r[1:0], median_frame_vsync};
            median_frame_href_r  <= {median_frame_href_r[1:0],  median_frame_href };
            median_frame_clken_r <= {median_frame_clken_r[1:0], median_frame_clken};
        end
    end

endmodule
