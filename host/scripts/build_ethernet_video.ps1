param([string]$ZigPath = $env:PREVIEW_ZIG)
$ErrorActionPreference = 'Stop'
if (-not $ZigPath) { $ZigPath = (Get-Command zig -ErrorAction Stop).Source }
$targets = @(
    @{ Output = 'pcie_multivideo_server.ethernet_v6'; Sources = @('pcie_multivideo_server.c', 'video_mosaic.c') },
    @{ Output = 'pcie_ethernet_status'; Sources = @('pcie_ethernet_status.c') },
    @{ Output = 'verify_multivideo_ethernet'; Sources = @('verify_multivideo_ethernet.c', 'video_mosaic.c') },
    @{ Output = 'verify_multivideo_overlap.ethernet_v6'; Sources = @('verify_multivideo_overlap.c', 'video_mosaic.c') }
)
foreach ($target in $targets) {
    $sourcePaths = @($target.Sources | ForEach-Object { Join-Path $PSScriptRoot $_ })
    $output = Join-Path $PSScriptRoot $target.Output
    & $ZigPath cc -target aarch64-linux-musl -static -O2 -std=c11 -Wall -Wextra -Werror -pthread @sourcePaths -o $output
    if ($LASTEXITCODE -ne 0) { throw "Cross compilation failed: $($target.Output)" }
    Get-Item -LiteralPath $output | Select-Object Name, Length
}
