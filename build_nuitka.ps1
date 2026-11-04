# Customer release builder: compiled one-file application plus verified portable runtime.
param(
  [string]$Py = "python",
  [string]$Ffmpeg = "",
  [string]$ReleaseDir = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
if (-not $ReleaseDir) { $ReleaseDir = Join-Path $Root "release_customer_$Stamp" }
$ReleaseDir = [IO.Path]::GetFullPath($ReleaseDir)
$BuildDir = Join-Path $Root "build_customer_$Stamp"

if (Test-Path -LiteralPath $ReleaseDir) {
  throw "Release directory already exists: $ReleaseDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "browser") -PathType Container)) {
  throw "Missing bundled browser directory."
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "extensions\pro\manifest.json") -PathType Leaf)) {
  throw "Missing extensions\pro."
}
if (-not $Ffmpeg) {
  $LocalFfmpeg = Join-Path $Root "ffmpeg.exe"
  if (Test-Path -LiteralPath $LocalFfmpeg -PathType Leaf) { $Ffmpeg = $LocalFfmpeg }
}
if (-not $Ffmpeg -or -not (Test-Path -LiteralPath $Ffmpeg -PathType Leaf)) {
  throw "Provide a valid ffmpeg.exe with -Ffmpeg <path>."
}
$Ffmpeg = (Resolve-Path -LiteralPath $Ffmpeg).Path

New-Item -ItemType Directory -Path $ReleaseDir | Out-Null
New-Item -ItemType Directory -Path $BuildDir | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ReleaseDir "extensions") | Out-Null

Copy-Item -LiteralPath (Join-Path $Root "browser") -Destination (Join-Path $ReleaseDir "browser") -Recurse
Copy-Item -LiteralPath (Join-Path $Root "extensions\pro") -Destination (Join-Path $ReleaseDir "extensions\pro") -Recurse
Copy-Item -LiteralPath $Ffmpeg -Destination (Join-Path $ReleaseDir "ffmpeg.exe")
Copy-Item -LiteralPath (Join-Path $Root "SKILL.md") -Destination (Join-Path $ReleaseDir "SKILL.md")
Copy-Item -LiteralPath (Join-Path $Root "HUONG_DAN_KHACH_HANG.txt") -Destination $ReleaseDir
Copy-Item -LiteralPath (Join-Path $Root "THIRD_PARTY_NOTICES.txt") -Destination $ReleaseDir

# Hash external assets before compilation. Values are compiled into the EXE,
# so replacing Chrome/FFmpeg/extension afterwards is detected at runtime.
& $Py (Join-Path $Root "make_bundle_hashes.py") `
  --root $ReleaseDir --output (Join-Path $Root "bundle_integrity_data.py")
if ($LASTEXITCODE -ne 0) { throw "Failed to generate bundle integrity values." }

$PatchrightNode = (& $Py -c "import patchright,os;print(os.path.join(os.path.dirname(patchright.__file__),'driver','node.exe'))").Trim()
if (-not (Test-Path -LiteralPath $PatchrightNode -PathType Leaf)) {
  throw "Patchright node.exe was not found."
}

Push-Location $Root
try {
  & $Py -m nuitka `
    --standalone --onefile --deployment --lto=yes `
    --enable-plugin=pyside6 `
    --assume-yes-for-downloads `
    --windows-console-mode=disable `
    --nofollow-import-to=tkinter --nofollow-import-to=customtkinter --nofollow-import-to=pytest --nofollow-import-to=unittest `
    --include-package=playwright --include-package=patchright `
    --include-package-data=playwright --include-package-data=patchright `
    --include-data-files="$PatchrightNode=patchright/driver/node.exe" `
    --include-package=win32com --include-module=pythoncom --include-module=pywintypes `
    --include-module=proxy_pool --include-module=proxyvn_api `
    --include-module=bundle_integrity --include-module=bundle_integrity_data `
    --include-package=google `
    --python-flag=isolated,safe_path,no_asserts,no_docstrings `
    --company-name="Seedance" --product-name="Seedance Auto" `
    --file-version=1.9.17 --product-version=1.9.17 `
    --output-dir=$BuildDir --output-filename=SeedanceAuto.exe `
    studio_qt.py
  if ($LASTEXITCODE -ne 0) { throw "Nuitka build failed with exit code $LASTEXITCODE." }
}
finally {
  Pop-Location
}

$BuiltExe = Join-Path $BuildDir "SeedanceAuto.exe"
if (-not (Test-Path -LiteralPath $BuiltExe -PathType Leaf)) {
  throw "Nuitka did not create SeedanceAuto.exe."
}
Copy-Item -LiteralPath $BuiltExe -Destination (Join-Path $ReleaseDir "SeedanceAuto.exe")

$Launcher = "@echo off`r`nstart `"`" `"%~dp0SeedanceAuto.exe`"`r`n"
[IO.File]::WriteAllText((Join-Path $ReleaseDir "Mo_SeedanceAuto.cmd"), $Launcher, [Text.Encoding]::ASCII)

$Archive = "$ReleaseDir.zip"
Compress-Archive -LiteralPath $ReleaseDir -DestinationPath $Archive -CompressionLevel Optimal
Write-Host "RELEASE=$ReleaseDir"
Write-Host "ARCHIVE=$Archive"
