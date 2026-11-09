$ErrorActionPreference = 'Stop'
$sceneRoot = $PSScriptRoot
$sceneCompiler = Join-Path $sceneRoot 'rk3568_lane_deploy\host_tools\ziglang\zig.exe'
$sceneOutput = Join-Path $sceneRoot 'rk3568_front_p1_deploy\host'
if (-not (Test-Path -LiteralPath $sceneCompiler)) { throw "Zig compiler missing: $sceneCompiler" }
New-Item -ItemType Directory -Path $sceneOutput -Force | Out-Null
Push-Location -LiteralPath $sceneRoot
try {
    & $sceneCompiler cc -O2 -std=c11 -Wall -Wextra -Werror -DFRONT_ONLY -DHAVE_FRONT_P1 -DHAVE_DRIVE_ASSIST rk_sync_control.c front_p1.c drive_assist.c -lws2_32 -o (Join-Path $sceneOutput 'rk_front_p1_control.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Offline scene controller build failed.' }
    Write-Output 'JUNCTION_DEMO_BUILD_PASS'
    Write-Output 'Run: python preview_junction_demo.py'
} finally { Pop-Location }
