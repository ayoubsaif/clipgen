# Build whisper.cpp with Vulkan for this computer, so Whisper can use the GPU on Windows on ARM.
# It installs nothing system-wide. The result goes to %LOCALAPPDATA%\clipgen\whisper.cpp.
$ErrorActionPreference = "Stop"

$Tag = "v1.9.5"
$Commit = "d1be6fde11ac6e0407606b4e42fe72d34add8037"
$Root = Join-Path $env:LOCALAPPDATA "clipgen\whisper.cpp"
$Source = Join-Path $Root "source-$Tag"
$Build = Join-Path $Root "build-$Tag"

$NativeArch = (Get-CimInstance Win32_Processor | Select-Object -First 1).Architecture
$Arch = if ($NativeArch -eq 12) { "ARM64" } else { "x64" }
$VcComponent = if ($Arch -eq "ARM64") { "Microsoft.VisualStudio.Component.VC.Tools.ARM64" } else { "Microsoft.VisualStudio.Component.VC.Tools.x86.x64" }

$Missing = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $Missing += "winget install --id Git.Git -e" }

$Vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$VsPath = if (Test-Path $Vswhere) { & $Vswhere -latest -products * -requires $VcComponent -property installationPath } else { $null }
if (-not $VsPath) {
    $Missing += "winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override `"--passive --wait --add $VcComponent --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.Windows11SDK.26100`""
}

$VulkanSdk = $env:VULKAN_SDK
if (-not $VulkanSdk -and (Test-Path "C:\VulkanSDK")) {
    $Latest = Get-ChildItem "C:\VulkanSDK" -Directory | Sort-Object { [version]($_.Name -replace '[^0-9.]', '') } | Select-Object -Last 1
    if ($Latest) { $VulkanSdk = $Latest.FullName }
}
if (-not $VulkanSdk) { $Missing += "winget install --id KhronosGroup.VulkanSDK -e" }

if ($Missing.Count) {
    Write-Host "Install these first, then open a new terminal and run this script again:" -ForegroundColor Yellow
    $Missing | ForEach-Object { Write-Host "  $_" }
    exit 1
}

$CMake = Join-Path $VsPath "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if (-not (Test-Path $CMake)) {
    $Found = Get-Command cmake -ErrorAction SilentlyContinue
    if (-not $Found) { throw "CMake was not found. Add the C++ CMake tools in Visual Studio Installer." }
    $CMake = $Found.Source
}

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE." }
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null
if (-not (Test-Path (Join-Path $Source ".git"))) {
    Invoke-Checked git @("-c", "advice.detachedHead=false", "clone", "--depth", "1", "--branch", $Tag, "https://github.com/ggml-org/whisper.cpp.git", $Source)
}
$Head = (& git -C $Source rev-parse HEAD).Trim()
if ($Head -ne $Commit) { throw "The whisper.cpp source is $Head, expected $Commit. Stopping." }

$env:VULKAN_SDK = $VulkanSdk
$env:PATH = (Join-Path $VulkanSdk "Bin") + ";" + $env:PATH
Invoke-Checked $CMake @("-S", $Source, "-B", $Build, "-A", $Arch, "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF",
    "-DGGML_VULKAN=ON", "-DGGML_NATIVE=OFF", "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_SERVER=OFF")
Invoke-Checked $CMake @("--build", $Build, "--config", "Release", "--target", "whisper-cli", "--parallel")

$Binary = Get-ChildItem $Build -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
if (-not $Binary) { throw "The build finished without whisper-cli.exe." }
Copy-Item $Binary.FullName (Join-Path $Root "whisper-cli.exe") -Force
Write-Host "whisper.cpp ($Arch, Vulkan) is ready: $(Join-Path $Root 'whisper-cli.exe')" -ForegroundColor Green
