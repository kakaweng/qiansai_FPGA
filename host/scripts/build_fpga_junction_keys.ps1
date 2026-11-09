$ErrorActionPreference='Stop'
$keyCompiler=Join-Path $PSScriptRoot 'rk3568_lane_deploy\host_tools\ziglang\zig.exe'
$keyOutput=Join-Path $PSScriptRoot 'rk3568_fpga_keys_deploy'
New-Item -ItemType Directory -Path $keyOutput -Force | Out-Null
& $keyCompiler cc -target aarch64-linux-musl -O2 -std=c11 -Wall -Wextra -Werror -static `
    (Join-Path $PSScriptRoot 'rk_fpga_keys.c') -o (Join-Path $keyOutput 'rk_fpga_keys')
if($LASTEXITCODE -ne 0){throw 'Key adapter cross compilation failed'}
$keyHash=(Get-FileHash -LiteralPath (Join-Path $keyOutput 'rk_fpga_keys') -Algorithm SHA256).Hash.ToLowerInvariant()
$keyMetadata=@{abi=1;rk_fpga_keys_sha256=$keyHash;target='aarch64-linux-musl';read_only=$true;timing_closed=$false;board_verified=$false;key_mapping=@{KEY5='turn_left';KEY6='straight';KEY7='turn_right'};legacy_keys_preserved=$true}
$keyFpga=Join-Path $PSScriptRoot '..\..\06_hdmi_loop_1\hdmi_loop.junction_keys_v2.sbit'
if(Test-Path -LiteralPath $keyFpga){
    $keyMetadata.fpga_file='06_hdmi_loop_1/hdmi_loop.junction_keys_v2.sbit'
    $keyMetadata.fpga_sha256=(Get-FileHash -LiteralPath $keyFpga -Algorithm SHA256).Hash.ToLowerInvariant()
    $keyMetadata.fpga_bytes=(Get-Item -LiteralPath $keyFpga).Length
}
$keyMetadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $keyOutput 'manifest.json') -Encoding ASCII
Write-Output "FPGA_KEY_BUILD_PASS $keyHash"
