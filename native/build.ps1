param([string]$Zig = $env:ZIG_EXE, [switch]$Test, [switch]$Live)
$ErrorActionPreference = 'Stop'
if (-not $Zig) { $Zig = (Get-Command zig -ErrorAction Stop).Source }
$projectRoot = Split-Path $PSScriptRoot -Parent
$buildRoot = Join-Path $projectRoot 'build\native'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$flags = @('-std=c++17','-target','x86_64-windows-gnu','-Oz','-s','-static','-fno-exceptions','-fno-rtti','-DJSON_NOEXCEPTION','-DUNICODE','-D_UNICODE','-Wall','-Wextra')
$libraries = @('-lwinhttp','-ladvapi32','-lshell32','-lole32','-luser32','-lgdi32','-lmsimg32')
Push-Location $PSScriptRoot
try {
    & $Zig rc '/fo' "$buildRoot\app.res" 'app.rc'
    if ($LASTEXITCODE) { throw 'Resource compilation failed' }
    & $Zig c++ @flags core.cpp windows.cpp app.cpp main.cpp "$buildRoot\app.res" @libraries '-municode' '-Wl,--subsystem,windows' '-o' "$buildRoot\DeepSeekBalanceMonitor.exe"
    if ($LASTEXITCODE) { throw 'Native build failed' }
    if ($Test) {
        & $Zig c++ @flags core.cpp tests/core_tests.cpp '-o' "$buildRoot\core_tests.exe"
        if ($LASTEXITCODE) { throw 'Core test build failed' }
        & "$buildRoot\core_tests.exe"
        if ($LASTEXITCODE) { throw 'Core tests failed' }
        & $Zig c++ @flags core.cpp windows.cpp tests/windows_tests.cpp @libraries '-municode' '-o' "$buildRoot\windows_tests.exe"
        if ($LASTEXITCODE) { throw 'Windows test build failed' }
        if ($Live) { & "$buildRoot\windows_tests.exe" '--live' } else { & "$buildRoot\windows_tests.exe" }
        if ($LASTEXITCODE) { throw 'Windows tests failed' }
        python tests/ui_smoke.py "$buildRoot\DeepSeekBalanceMonitor.exe"
        if ($LASTEXITCODE) { throw 'UI smoke failed' }
    }
    $artifact = Get-Item -LiteralPath "$buildRoot\DeepSeekBalanceMonitor.exe"
    if ($artifact.Length -ge 1MB) { throw "Executable exceeds 1 MiB goal: $($artifact.Length) bytes" }
    Write-Output "Native EXE: $($artifact.FullName) ($($artifact.Length) bytes)"
} finally { Pop-Location }
