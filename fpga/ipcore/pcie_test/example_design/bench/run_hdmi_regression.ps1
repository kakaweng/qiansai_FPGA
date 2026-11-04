param(
    [string]$IverilogDirectory = '',
    [string]$OutputDirectory = (Join-Path $env:TEMP 'pango_hdmi_regression')
)

$ErrorActionPreference = 'Stop'
if ($IverilogDirectory) {
    $env:PATH = "$IverilogDirectory;" + $env:PATH
}
$compiler = (Get-Command iverilog.exe -ErrorAction Stop).Source
$simulator = (Get-Command vvp.exe -ErrorAction Stop).Source
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$ddrRoot = Join-Path $projectRoot 'src\ddr'
$bridge = Join-Path $projectRoot 'src\pcie\pcie_frame_dma_bridge.v'
$ramModel = Join-Path $PSScriptRoot 'pcie_hdmi_ddr_tb.v'
$tests = @(
    @{
        Top = 'hdmi_frame_pause_tb'
        Sources = @(
            (Join-Path $PSScriptRoot 'hdmi_frame_pause_tb.v'),
            (Join-Path $ddrRoot 'wr_buf.v'),
            (Join-Path $projectRoot 'sim\ddr_shared_writer_model.v'),
            (Join-Path $ddrRoot 'ddr_write_arbiter.v'),
            (Join-Path $ddrRoot 'wr_rd_ctrl_top.v'),
            (Join-Path $ddrRoot 'wr_cmd_trans.v'),
            (Join-Path $ddrRoot 'wr_ctrl.v'),
            (Join-Path $ddrRoot 'rd_ctrl.v')
        )
    },
    @{
        Top = 'ddr_read_command_tb'
        Sources = @(
            (Join-Path $PSScriptRoot 'ddr_read_command_tb.v'),
            (Join-Path $ddrRoot 'wr_cmd_trans.v')
        )
    },
    @{
        Top = 'pcie_hdmi_start_tb'
        Sources = @(
            (Join-Path $PSScriptRoot 'pcie_hdmi_start_tb.v'),
            $ramModel,
            $bridge,
            (Join-Path $projectRoot 'ipcore\pcie_test\example_design\rtl\ipsl_pcie_dma_ctrl\ipsl_pcie_dma_wr_ctrl.v')
        )
    },
    @{
        Top = 'pcie_hdmi_ddr_tb'
        Sources = @(
            $ramModel,
            $bridge,
            (Join-Path $ddrRoot 'fram_buf.v'),
            (Join-Path $ddrRoot 'wr_rd_ctrl_top.v'),
            (Join-Path $ddrRoot 'wr_cmd_trans.v'),
            (Join-Path $ddrRoot 'rd_ctrl.v'),
            (Join-Path $ddrRoot 'wr_ctrl.v'),
            (Join-Path $ddrRoot 'rd_buf.v'),
            (Join-Path $ddrRoot 'wr_buf.v')
        )
    }
)

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
foreach ($test in $tests) {
    $compiledTest = Join-Path $OutputDirectory ($test.Top + '.vvp')
    & $compiler -g2012 -s $test.Top -o $compiledTest @($test.Sources)
    if ($LASTEXITCODE -ne 0) {
        throw "Compilation failed: $($test.Top)"
    }
    & $simulator $compiledTest
    if ($LASTEXITCODE -ne 0) {
        throw "Simulation failed: $($test.Top)"
    }
    if ($test.Top -eq 'pcie_hdmi_ddr_tb') {
        & $compiler -g2012 -s $test.Top '-Ppcie_hdmi_ddr_tb.RGB565=1' -o $compiledTest @($test.Sources)
        if ($LASTEXITCODE -ne 0) {
            throw 'RGB565 compilation failed'
        }
        & $simulator $compiledTest
        if ($LASTEXITCODE -ne 0) {
            throw 'RGB565 simulation failed'
        }
        & $compiler -g2012 -s $test.Top '-Ppcie_hdmi_ddr_tb.RGB565=1' '-Ppcie_hdmi_ddr_tb.CAMERA=1' -o $compiledTest @($test.Sources)
        if ($LASTEXITCODE -ne 0) {
            throw 'Camera source compilation failed'
        }
        & $simulator $compiledTest
        if ($LASTEXITCODE -ne 0) {
            throw 'Camera source simulation failed'
        }
        & $compiler -g2012 -s $test.Top '-Ppcie_hdmi_ddr_tb.RGB565=1' '-Ppcie_hdmi_ddr_tb.CAMERA=1' '-Ppcie_hdmi_ddr_tb.ETHERNET=1' -o $compiledTest @($test.Sources)
        if ($LASTEXITCODE -ne 0) { throw 'Ethernet source compilation failed' }
        & $simulator $compiledTest
        if ($LASTEXITCODE -ne 0) { throw 'Ethernet source simulation failed' }
    }
    if ($test.Top -eq 'hdmi_frame_pause_tb') {
        & $compiler -g2012 -s $test.Top '-Phdmi_frame_pause_tb.FRAME_WIDTH=1920' '-Phdmi_frame_pause_tb.FRAME_HEIGHT=8' -o $compiledTest @($test.Sources)
        if ($LASTEXITCODE -ne 0) {
            throw '1080p line-width pause compilation failed'
        }
        & $simulator $compiledTest
        if ($LASTEXITCODE -ne 0) {
            throw '1080p line-width pause simulation failed'
        }
        & $compiler -g2012 -s $test.Top '-Phdmi_frame_pause_tb.FRAME_WIDTH=1920' '-Phdmi_frame_pause_tb.FRAME_HEIGHT=8' '-Phdmi_frame_pause_tb.USE_AXI=1' -o $compiledTest @($test.Sources)
        if ($LASTEXITCODE -ne 0) {
            throw 'Shared DDR HDMI writer compilation failed'
        }
        & $simulator $compiledTest
        if ($LASTEXITCODE -ne 0) {
            throw 'Shared DDR HDMI writer simulation failed'
        }
        & $compiler -g2012 -s $test.Top '-Phdmi_frame_pause_tb.FRAME_WIDTH=1920' '-Phdmi_frame_pause_tb.FRAME_HEIGHT=8' '-Phdmi_frame_pause_tb.USE_AXI=1' `
            '-Phdmi_frame_pause_tb.PIXEL_HALF_PERIOD=3.367' '-Phdmi_frame_pause_tb.DDR_HALF_PERIOD=5.0' `
            '-Phdmi_frame_pause_tb.LINE_BLANK_CYCLES=280' -o $compiledTest @($test.Sources)
        if ($LASTEXITCODE -ne 0) {
            throw '148.5 MHz HDMI / 100 MHz DDR compilation failed'
        }
        & $simulator $compiledTest
        if ($LASTEXITCODE -ne 0) {
            throw '148.5 MHz HDMI / 100 MHz DDR simulation failed'
        }
    }
}
