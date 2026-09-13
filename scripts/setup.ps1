[CmdletBinding()]
param(
    [string]$Python,
    [string]$Node,
    [string]$FfmpegDirectory,
    [string]$BrowserExecutable,
    [string]$PlaywrightBrowsersPath,
    [switch]$SkipBrowsers
)
$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $PSScriptRoot
$engineRoot = Join-Path $PSScriptRoot 'engine'
$workbenchRoot = Join-Path $skillRoot 'vendor\video-shotcraft\workbench'
$localConfigPath = Join-Path $skillRoot '.runtime.local.json'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_CACHE_DIR = Join-Path $skillRoot '.cache\pip'
$env:npm_config_cache = Join-Path $skillRoot '.cache\npm'

function Resolve-Tool([string]$Specified, [string]$DefaultName) {
    if ($Specified) {
        if (Test-Path -LiteralPath $Specified -PathType Leaf) { return (Resolve-Path -LiteralPath $Specified).Path }
        throw "Executable not found: $Specified"
    }
    $found = Get-Command $DefaultName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) { throw "Specify the installed executable for $DefaultName. No system runtime will be installed." }
    return $found.Source
}
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Executable" }
}

$savedConfig = @{}
if (Test-Path -LiteralPath $localConfigPath) {
    $saved = Get-Content -LiteralPath $localConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($property in $saved.PSObject.Properties) { $savedConfig[$property.Name] = $property.Value }
}
if (-not $Python) { $Python = $savedConfig.Python }
if (-not $Node) { $Node = $savedConfig.Node }
if (-not $FfmpegDirectory) { $FfmpegDirectory = $savedConfig.FfmpegDirectory }
if (-not $BrowserExecutable) { $BrowserExecutable = $savedConfig.BrowserExecutable }
if (-not $PlaywrightBrowsersPath) { $PlaywrightBrowsersPath = $savedConfig.PlaywrightBrowsersPath }
if (-not $PlaywrightBrowsersPath) { $PlaywrightBrowsersPath = Join-Path $skillRoot '.cache\playwright' }
$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersPath
if (-not $SkipBrowsers -and [IO.Path]::GetFullPath($PlaywrightBrowsersPath) -ne [IO.Path]::GetFullPath((Join-Path $skillRoot '.cache\playwright'))) {
    throw 'An external Playwright browser cache is read-only here. Use -SkipBrowsers to reuse it without installation or cleanup.'
}
$Python = Resolve-Tool $Python 'python.exe'
$Node = Resolve-Tool $Node 'node.exe'
Invoke-Checked $Python @('-c', 'import sys; raise SystemExit(sys.version_info < (3,11))')
Invoke-Checked $Node @('-e', 'process.exit(Number(process.versions.node.split(".")[0]) < 22 ? 1 : 0)')
$npmCli = Join-Path (Split-Path -Parent $Node) 'node_modules\npm\bin\npm-cli.js'
if (-not (Test-Path -LiteralPath $npmCli -PathType Leaf)) { throw 'The selected Node installation must include npm. Pass a complete Node distribution.' }
$env:PATH = (Split-Path -Parent $Node) + [IO.Path]::PathSeparator + $env:PATH
if ($FfmpegDirectory) {
    $FfmpegDirectory = (Resolve-Path -LiteralPath $FfmpegDirectory).Path
    foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $FfmpegDirectory $tool) -PathType Leaf)) { throw "Missing $tool in the specified FFmpeg directory." }
    }
    $env:PATH = $FfmpegDirectory + [IO.Path]::PathSeparator + $env:PATH
}
foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) { $null = Resolve-Tool '' $tool }
if ($BrowserExecutable) { $BrowserExecutable = Resolve-Tool $BrowserExecutable 'chrome.exe' }

$venvRoot = Join-Path $engineRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
if ((Test-Path -LiteralPath $venvRoot) -and -not (Test-Path -LiteralPath $venvPython)) {
    throw 'The project .venv is incomplete or belongs to another OS. Inspect it before retrying; nothing was removed.'
}
if (-not (Test-Path -LiteralPath $venvPython)) { Invoke-Checked $Python @('-m', 'venv', $venvRoot) }
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check', '-e', $engineRoot)
Invoke-Checked $venvPython @('-m', 'pip', 'check')
if (-not $SkipBrowsers) { Invoke-Checked $venvPython @('-m', 'playwright', 'install', 'chromium') }

Push-Location -LiteralPath $workbenchRoot
try {
    # Use Node's npm CLI directly: no cmd.exe interpolation or npm.cmd quoting.
    Invoke-Checked $Node @($npmCli, 'ci', '--no-audit', '--no-fund')
    Invoke-Checked $Node @('scripts/gen-index.mjs')
    if (-not $SkipBrowsers -and -not $BrowserExecutable) {
        Invoke-Checked $Node @('node_modules/@remotion/cli/remotion-cli.js', 'browser', 'ensure')
    }
} finally { Pop-Location }
$savedConfig = [ordered]@{ Python = $Python; Node = $Node; FfmpegDirectory = $FfmpegDirectory; BrowserExecutable = $BrowserExecutable; PlaywrightBrowsersPath = $PlaywrightBrowsersPath }
[IO.File]::WriteAllText($localConfigPath, ($savedConfig | ConvertTo-Json) + [Environment]::NewLine, $utf8)
Invoke-Checked $venvPython @('-m', 'product_video', '--help')
Write-Host 'Project environment is ready. No speech API or credentials were accessed.'
