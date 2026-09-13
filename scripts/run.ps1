# All remaining arguments are passed unchanged to product-video.
$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot 'engine\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Run scripts/setup.ps1 with an existing Python 3.11+, Node 22+ and FFmpeg installation first.'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $skillRoot '.cache\playwright'
$localConfigPath = Join-Path $skillRoot '.runtime.local.json'
if (Test-Path -LiteralPath $localConfigPath) {
    $config = Get-Content -LiteralPath $localConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($config.PlaywrightBrowsersPath) { $env:PLAYWRIGHT_BROWSERS_PATH = $config.PlaywrightBrowsersPath }
    if ($config.Node) {
        $env:PRODUCT_VIDEO_NODE = $config.Node
        $env:PATH = (Split-Path -Parent $config.Node) + [IO.Path]::PathSeparator + $env:PATH
    }
    if ($config.FfmpegDirectory) {
        $env:PRODUCT_VIDEO_FFMPEG = Join-Path $config.FfmpegDirectory 'ffmpeg.exe'
        $env:PRODUCT_VIDEO_FFPROBE = Join-Path $config.FfmpegDirectory 'ffprobe.exe'
        $env:PATH = $config.FfmpegDirectory + [IO.Path]::PathSeparator + $env:PATH
    }
    if ($config.BrowserExecutable) { $env:PRODUCT_VIDEO_BROWSER = $config.BrowserExecutable }
}
& $pythonPath -m product_video @args
if ($LASTEXITCODE -ne 0) { throw "product-video failed (exit $LASTEXITCODE)." }
