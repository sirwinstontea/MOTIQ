# Build installer\dist\MOTIQ-Setup.exe from this checkout.
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
# Needs: git, uv (https://docs.astral.sh/uv/), Inno Setup 6.3+ (ISCC.exe on PATH, in its default folder, or $env:ISCC).
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$stage = Join-Path $here 'stage'
$build = Join-Path $here 'build'
$uvVersion = '0.12.19'

# 1. Stage a clean copy: tracked + new files, nothing gitignored (.env keys, venvs, node_modules, .motiq)
Write-Host '== staging files'
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage | Out-Null
$files = git -C $root ls-files -co --exclude-standard
foreach ($f in $files) {
  if ($f -match '(^|/)(\.DS_Store|__pycache__/|installer/)' -or $f -match '\.pyc$') { continue }
  $src = Join-Path $root $f
  if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
  $dst = Join-Path $stage $f
  New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
  Copy-Item -LiteralPath $src -Destination $dst
}

# 2. MOTIQ.exe = the launcher (standard library only) frozen with PyInstaller, no console window
Write-Host '== building MOTIQ.exe'
uv run --no-project --python 3.12 --with 'pyinstaller>=6.10' pyinstaller --noconfirm --clean --onefile --windowed `
  --name MOTIQ --icon (Join-Path $here 'motiq.ico') --paths (Join-Path $root 'launcher') `
  --distpath $build --workpath (Join-Path $build 'work') --specpath $build (Join-Path $root 'launcher\motiq.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
Copy-Item (Join-Path $build 'MOTIQ.exe') $stage

# 3. uv.exe, so the wizard can install Python 3.12 and the packages without anything preinstalled
Write-Host '== bundling uv'
$tools = Join-Path $stage 'tools'
New-Item -ItemType Directory -Force $tools | Out-Null
$zip = Join-Path $build 'uv.zip'
Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
Expand-Archive $zip -DestinationPath (Join-Path $build 'uv') -Force
Copy-Item (Join-Path $build 'uv\uv.exe') $tools

# 4. Compile the installer
Write-Host '== compiling installer'
$iscc = $env:ISCC
if (-not $iscc) { $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }
if (-not $iscc) {
  foreach ($c in @("$env:USERPROFILE\App files\Claude extras\Tools\InnoSetup\ISCC.exe",
                   "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                   "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 7\ISCC.exe")) {
    if (Test-Path $c) { $iscc = $c; break }
  }
}
if (-not $iscc) { throw 'Inno Setup not found. Install it from https://jrsoftware.org/isdl.php or set $env:ISCC.' }
& $iscc (Join-Path $here 'motiq.iss')
if ($LASTEXITCODE -ne 0) { throw 'ISCC failed' }
Write-Host "== done: $(Join-Path $here 'dist\MOTIQ-Setup.exe')"
