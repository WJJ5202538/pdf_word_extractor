#Requires -Version 5.1
<#
.SYNOPSIS
    Build a wheel and an sdist for pdf-word-extractor.

.DESCRIPTION
    Uses .venv when it exists, otherwise the python on PATH.
    The build is isolated by default; pass -NoIsolation to reuse the
    current environment (requires setuptools, wheel and build).

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Clean
    .\build.ps1 -NoIsolation
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$NoIsolation
)

$ErrorActionPreference = "Stop"
Push-Location -LiteralPath $PSScriptRoot
try {
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $python = $venvPython
    }
    else {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if (-not $command) {
            throw "Python was not found. Install Python 3.10+ or create .venv first."
        }
        $python = $command.Source
    }

    Write-Host "Using: $python"
    & $python -c "import sys; print('Python', sys.version.split()[0])"
    if ($LASTEXITCODE -ne 0) { throw "Python is not runnable." }

    & $python -c "import build" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing build backend (build)..."
        & $python -m pip install --upgrade "build>=1.2"
        if ($LASTEXITCODE -ne 0) { throw "Failed to install build." }
    }

    if ($Clean) {
        foreach ($dir in @("build", "dist")) {
            $target = Join-Path $PSScriptRoot $dir
            if (Test-Path -LiteralPath $target) {
                Remove-Item -LiteralPath $target -Recurse -Force
                Write-Host "Removed $dir"
            }
        }
        Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "src") -Filter "*.egg-info" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force
                Write-Host "Removed src/$($_.Name)"
            }
    }

    $buildArgs = @("-m", "build")
    if ($NoIsolation) { $buildArgs += "--no-isolation" }

    Write-Host "Running: $python $($buildArgs -join ' ')"
    & $python @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE." }

    Write-Host ""
    Write-Host "Build artifacts:"
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "dist") |
        Select-Object Name, Length, LastWriteTime |
        Format-Table -AutoSize
}
finally {
    Pop-Location
}
