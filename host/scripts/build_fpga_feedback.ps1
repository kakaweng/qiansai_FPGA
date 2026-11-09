$ErrorActionPreference='Stop'
$feedbackCompiler=Join-Path $PSScriptRoot 'rk3568_lane_deploy\host_tools\ziglang\zig.exe'
$feedbackOutput=Join-Path $PSScriptRoot 'rk3568_fpga_feedback_deploy'
New-Item -ItemType Directory -Path $feedbackOutput -Force | Out-Null
$feedbackNative=Join-Path $feedbackOutput 'feedback_protocol_test.exe'
& $feedbackCompiler cc -O2 -std=c11 -Wall -Wextra -Werror `
    (Join-Path $PSScriptRoot 'tests\fpga_feedback_protocol_test.c') -o $feedbackNative
if($LASTEXITCODE -ne 0){throw 'Feedback protocol compilation failed'}
& $feedbackNative
if($LASTEXITCODE -ne 0){throw 'Feedback protocol test failed'}
& $feedbackCompiler cc -target aarch64-linux-musl -O2 -std=c11 -Wall -Wextra -Werror -static `
    (Join-Path $PSScriptRoot 'rk_fpga_feedback.c') -o (Join-Path $feedbackOutput 'rk_fpga_feedback')
if($LASTEXITCODE -ne 0){throw 'Feedback adapter cross compilation failed'}
$feedbackHash=(Get-FileHash -LiteralPath (Join-Path $feedbackOutput 'rk_fpga_feedback') -Algorithm SHA256).Hash.ToLowerInvariant()
@{abi=1;rk_fpga_feedback_sha256=$feedbackHash;target='aarch64-linux-musl';bar1_write_offset='0x180';lease_ms=1500;board_verified=$false} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $feedbackOutput 'manifest.json') -Encoding ASCII
Write-Output "FPGA_FEEDBACK_BUILD_PASS $feedbackHash"
