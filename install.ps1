<#
    Collage Studio — one-press setup for Windows.

    Right-click this file -> "Run with PowerShell", or from a terminal:

        powershell -ExecutionPolicy Bypass -File install.ps1

    It will:
      1. create the target folder (default C:\Zapps\collage-studio)
      2. download index.html, render_collage.py, the example layout and README
      3. check for Python and ffmpeg, and tell you exactly what is missing
      4. open the board in your browser

    Nothing is installed without asking. Nothing outside the target folder
    is modified.
#>

[CmdletBinding()]
param(
    [string] $Target = "C:\Zapps\collage-studio",
    [string] $Branch = "main",
    [switch] $NoOpen,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------- helpers -- #

function Write-Head($text) {
    Write-Host ""
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ("  " + ("-" * $text.Length)) -ForegroundColor DarkCyan
}

function Write-Ok($text)   { Write-Host "  [ok]   $text" -ForegroundColor Green }
function Write-Warn2($text){ Write-Host "  [warn] $text" -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "  [fail] $text" -ForegroundColor Red }
function Write-Info($text) { Write-Host "         $text" -ForegroundColor Gray }

function Have($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# ------------------------------------------------------------------ target -- #

Write-Host ""
Write-Host "  Collage Studio setup" -ForegroundColor White
Write-Host "  ====================" -ForegroundColor DarkGray

Write-Head "Target folder"
if (Test-Path -LiteralPath $Target) {
    Write-Ok "exists: $Target"
} else {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    Write-Ok "created: $Target"
}

if (-not $Force) {
    $existing = Get-ChildItem -LiteralPath $Target -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Warn2 "the folder is not empty - existing files with the same names will be overwritten"
        $answer = Read-Host "         Continue? [Y/n]"
        if ($answer -and $answer.Trim().ToLower() -ne 'y' -and $answer.Trim().ToLower() -ne 'yes') {
            Write-Host "  Cancelled." -ForegroundColor Yellow
            exit 0
        }
    }
}

# ---------------------------------------------------------------- download -- #

Write-Head "Downloading files"

$repo   = "LLamasTwitchin/collage-studio"
$base   = "https://raw.githubusercontent.com/$repo/$Branch"

$files = @(
    @{ Path = "index.html";                Desc = "the interactive board" },
    @{ Path = "render_collage.py";         Desc = "the Python renderer" },
    @{ Path = "README.md";                 Desc = "documentation" },
    @{ Path = "examples/hero-layout.json"; Desc = "example layout" }
)

# TLS 1.2 for older PowerShell hosts that still default to 1.0
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

$downloaded = 0
$failed     = @()

foreach ($f in $files) {
    $url  = "$base/$($f.Path)"
    $dest = Join-Path $Target ($f.Path -replace '/', '\')
    $dir  = Split-Path -Parent $dest

    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }

    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -ErrorAction Stop
        $size = (Get-Item -LiteralPath $dest).Length
        Write-Ok ("{0,-26} {1,8:N0} bytes  - {2}" -f $f.Path, $size, $f.Desc)
        $downloaded++
    } catch {
        Write-Err "$($f.Path)  ($($_.Exception.Message))"
        $failed += $f.Path
    }
}

if ($failed.Count -eq $files.Count) {
    Write-Host ""
    Write-Err "Nothing downloaded. Check your internet connection, or that the repo"
    Write-Info "and branch '$Branch' exist: https://github.com/$repo"
    exit 1
}
if ($failed.Count -gt 0) {
    Write-Warn2 "some files failed: $($failed -join ', ')"
}

# ------------------------------------------------------------ requirements -- #

Write-Head "Checking requirements"

# --- Python ---------------------------------------------------------------- #
$pythonCmd = $null
foreach ($cand in @('python', 'py', 'python3')) {
    if (Have $cand) { $pythonCmd = $cand; break }
}

if ($pythonCmd) {
    try {
        $ver = & $pythonCmd --version 2>&1
        Write-Ok "Python: $ver  (command: $pythonCmd)"
    } catch {
        Write-Warn2 "found '$pythonCmd' but it would not run"
        $pythonCmd = $null
    }
} else {
    Write-Warn2 "Python not found - only needed for the renderer, not the board"
    Write-Info 'install:  winget install Python.Python.3.12'
    Write-Info '     or:  https://www.python.org/downloads/'
    Write-Info 'then reopen this terminal so PATH updates.'
}

# --- ffmpeg ---------------------------------------------------------------- #
if (Have 'ffmpeg') {
    $ff = (& ffmpeg -version 2>&1 | Select-Object -First 1)
    Write-Ok "ffmpeg: $ff"
} else {
    Write-Warn2 "ffmpeg not found - needed to render video from a layout"
    Write-Info 'install:  winget install Gyan.FFmpeg'
    Write-Info 'then reopen this terminal so PATH updates.'
}

# --- sanity check the renderer without needing ffmpeg ---------------------- #
$renderer = Join-Path $Target "render_collage.py"
if ((Test-Path -LiteralPath $renderer) -and $pythonCmd) {
    try {
        & $pythonCmd -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read()); print('ok')" $renderer 2>&1 | Out-Null
        Write-Ok "renderer parses cleanly"
    } catch {
        Write-Warn2 "renderer did not parse - send me the error and I will fix it"
    }
}

# -------------------------------------------------------------------- note -- #

Write-Head "Note on the renderer"

$rendererV2 = Join-Path $Target "render_collage_v2.py"
Write-Info "This installs the STABLE renderer (render_collage.py)."
Write-Info "An extended, UNVERIFIED version lives on the 'v2-extended' branch:"
Write-Info "    $base/render_collage_v2.py"
Write-Info "It adds transitions, Ken Burns, keyframe motion and effects, but its"
Write-Info "ffmpeg filter expressions have never been run. Test it before relying on it."

# ------------------------------------------------------------------ launch -- #

$board = Join-Path $Target "index.html"

Write-Head "Done"
Write-Info "folder:   $Target"
Write-Info "board:    $board"
Write-Info "docs:     $(Join-Path $Target 'README.md')"

if ($downloaded -gt 0) {
    Write-Host ""
    Write-Host "  Next steps" -ForegroundColor White
    Write-Info "1. drop images and MP4s onto the board, arrange, hit Play"
    Write-Info "2. 'Save layout' -> download layout.json into this same folder"
    Write-Info "3. put your media in the folder too, then:"
    Write-Info "     cd `"$Target`""
    Write-Info "     $pythonCmd render_collage.py layout.json -o collage.mp4"
}

if (-not $NoOpen -and (Test-Path -LiteralPath $board)) {
    Write-Host ""
    Write-Info "opening the board in your browser..."
    Start-Process $board
}

Write-Host ""
