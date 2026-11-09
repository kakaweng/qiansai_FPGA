$ErrorActionPreference = 'Stop'
$liveRoot = $PSScriptRoot
$liveCompiler = Join-Path $liveRoot 'rk3568_lane_deploy\host_tools\ziglang\zig.exe'
$liveOutput = Join-Path $liveRoot 'rk3568_front_p1_deploy\host'
if (-not (Test-Path -LiteralPath $liveCompiler)) { throw "Zig compiler missing: $liveCompiler" }
New-Item -ItemType Directory -Path $liveOutput -Force | Out-Null
Push-Location -LiteralPath $liveRoot
try {
    & $liveCompiler cc -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror -I. tests/front_world_test.c -o (Join-Path $liveOutput 'front_world_test.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Live-world protocol test build failed.' }
    & (Join-Path $liveOutput 'front_world_test.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Live-world protocol test failed.' }
    & $liveCompiler cc -O2 -std=c11 -Wall -Wextra -Werror -shared -I. tests/live_road_bridge.c lane_style_core.c -o (Join-Path $liveOutput 'live_road_bridge.dll')
    if ($LASTEXITCODE -ne 0) { throw 'Live-road replay bridge build failed.' }
    Write-Output 'LIVE_ROAD_BUILD_PASS'
} finally { Pop-Location }
