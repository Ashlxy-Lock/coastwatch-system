$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
# The bundled MinGW used on this workstation cannot open non-ASCII source
# paths. Stage only these four host-test inputs under the ASCII temp path.
$stage = Join-Path ([IO.Path]::GetTempPath()) 'coastwatch-stm32-host'
$includeDir = Join-Path $stage 'include'
$sourceDir = Join-Path $stage 'src'
New-Item -ItemType Directory -Force -Path $includeDir, $sourceDir | Out-Null
Copy-Item -Force -LiteralPath (Join-Path $projectRoot 'include\app_config.h') -Destination $includeDir
Copy-Item -Force -LiteralPath (Join-Path $projectRoot 'include\coastwatch_logic.h') -Destination $includeDir
Copy-Item -Force -LiteralPath (Join-Path $projectRoot 'include\protocol.h') -Destination $includeDir
Copy-Item -Force -LiteralPath (Join-Path $projectRoot 'src\coastwatch_logic.cpp') -Destination $sourceDir
Copy-Item -Force -LiteralPath (Join-Path $projectRoot 'src\protocol.cpp') -Destination $sourceDir
Copy-Item -Force -LiteralPath (Join-Path $projectRoot 'test\host\test_main.cpp') -Destination $sourceDir
$logicSource = Join-Path $sourceDir 'coastwatch_logic.cpp'
$protocolSource = Join-Path $sourceDir 'protocol.cpp'
$testSource = Join-Path $sourceDir 'test_main.cpp'
$testExe = Join-Path $stage 'stm32_logic_tests.exe'

$gxx = Get-Command g++.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $gxx) {
    $openMvGxx = 'F:\OpenMv\share\qtcreator\stedgeai\Utilities\windows\mingw64\bin\g++.exe'
    if (Test-Path -LiteralPath $openMvGxx) {
        $gxx = $openMvGxx
    }
}

if ($gxx) {
    $oldPath = $env:PATH
    try {
        $env:PATH = (Split-Path -Parent $gxx) + [IO.Path]::PathSeparator + $env:PATH
        $arguments = @('-std=gnu++14', '-Wall', '-Wextra', '-Werror')
        # The OpenMV-bundled MinGW keeps libstdc++ headers in a nonstandard
        # location. A normal PATH-installed g++ does not need these additions.
        $bundledCpp = Join-Path (Split-Path -Parent (Split-Path -Parent $gxx)) 'lib\gcc\x86_64-w64-mingw32\11.2.0\include\c++'
        if (Test-Path -LiteralPath (Join-Path $bundledCpp 'cstddef')) {
            $arguments += "-isystem$bundledCpp"
            $arguments += "-isystem$(Join-Path $bundledCpp 'x86_64-w64-mingw32')"
            $arguments += "-isystem$(Join-Path $bundledCpp 'backward')"
        }
        $arguments += "-I$includeDir", $logicSource, $protocolSource, $testSource, "-o$testExe"
        & $gxx $arguments
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
        & $testExe
        exit $LASTEXITCODE
    }
    finally {
        $env:PATH = $oldPath
    }
}

throw 'No runnable host C++ compiler was found. Install g++ or add it to PATH.'
