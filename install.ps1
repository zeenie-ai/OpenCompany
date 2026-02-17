# MachinaOS Installer for Windows
# Usage: iwr -useb https://raw.githubusercontent.com/trohitg/MachinaOS/main/install.ps1 | iex
#
# This script installs MachinaOS and its dependencies:
# - Node.js 22+ (via winget/choco)
# - Python 3.12+ (via winget/choco)
# - uv (Python package manager)

$ErrorActionPreference = "Stop"

$MIN_NODE_VERSION = 22
$MIN_PYTHON_VERSION = "3.12"

# Colors
function Write-Color {
    param([string]$Text, [string]$Color = "White")
    Write-Host $Text -ForegroundColor $Color
}

function Info { Write-Color "[INFO] $args" "Cyan" }
function Success { Write-Color "[OK] $args" "Green" }
function Warn { Write-Color "[WARN] $args" "Yellow" }
function Error-Exit { Write-Color "[ERROR] $args" "Red"; exit 1 }

# Banner
Write-Host ""
Write-Color "  __  __            _     _             ___  ____  " "Cyan"
Write-Color " |  \/  | __ _  ___| |__ (_)_ __   __ _/ _ \/ ___| " "Cyan"
Write-Color " | |\/| |/ _`` |/ __| '_ \| | '_ \ / _`` | | | \___ \ " "Cyan"
Write-Color " | |  | | (_| | (__| | | | | | | | (_| | |_| |___) |" "Cyan"
Write-Color " |_|  |_|\__,_|\___|_| |_|_|_| |_|\__,_|\___/|____/ " "Cyan"
Write-Host ""
Write-Host "Open-source workflow automation with AI agents"
Write-Host ""

# Check if command exists
function Has-Command {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

# Get package manager
function Get-PackageManager {
    if (Has-Command "winget") { return "winget" }
    if (Has-Command "choco") { return "choco" }
    return $null
}

# Refresh PATH from registry
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# =============================================================================
# Dependency Checks and Installation
# =============================================================================

function Check-Node {
    if (Has-Command "node") {
        $version = (node --version) -replace "v", ""
        $major = [int]($version.Split(".")[0])
        if ($major -ge $MIN_NODE_VERSION) {
            Success "Node.js v$version"
            return $true
        }
        Warn "Node.js v$version is too old (need v$MIN_NODE_VERSION+)"
    }
    return $false
}

function Install-Node {
    Info "Installing Node.js $MIN_NODE_VERSION..."
    $pm = Get-PackageManager

    switch ($pm) {
        "winget" {
            winget install OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
        }
        "choco" {
            choco install nodejs-lts -y
        }
        default {
            Error-Exit "Please install winget or chocolatey, or install Node.js manually from https://nodejs.org/"
        }
    }

    Refresh-Path

    if (-not (Check-Node)) {
        Error-Exit "Failed to install Node.js. Please install manually and restart PowerShell."
    }
}

function Check-Python {
    foreach ($cmd in @("python", "python3")) {
        if (Has-Command $cmd) {
            $versionOutput = & $cmd --version 2>&1
            if ($versionOutput -match "Python (\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 12) {
                    Success "Python $major.$minor ($cmd)"
                    $script:PYTHON_CMD = $cmd
                    return $true
                }
            }
        }
    }
    Warn "Python $MIN_PYTHON_VERSION+ not found"
    return $false
}

function Install-Python {
    Info "Installing Python $MIN_PYTHON_VERSION..."
    $pm = Get-PackageManager

    switch ($pm) {
        "winget" {
            winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        }
        "choco" {
            choco install python312 -y
        }
        default {
            Error-Exit "Please install winget or chocolatey, or install Python manually from https://python.org/"
        }
    }

    Refresh-Path

    if (-not (Check-Python)) {
        Error-Exit "Failed to install Python. Please install manually and restart PowerShell."
    }
}

function Check-Uv {
    if (Has-Command "uv") {
        $version = (uv --version) -replace "uv ", ""
        Success "uv $version"
        return $true
    }
    return $false
}

function Install-Uv {
    Info "Installing uv (Python package manager)..."

    # Try pip first
    if ($script:PYTHON_CMD) {
        try {
            & $script:PYTHON_CMD -m pip install uv 2>&1 | Out-Null
            Refresh-Path
            if (Check-Uv) { return }
        } catch {}
    }

    # Fallback to official installer
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

    if (-not (Check-Uv)) {
        Error-Exit "Failed to install uv"
    }
}

# =============================================================================
# Main Installation Flow
# =============================================================================

function Main {
    Write-Host ""
    Info "Checking dependencies..."
    Write-Host ""

    # Check and install dependencies
    if (-not (Check-Node)) { Install-Node }
    if (-not (Check-Python)) { Install-Python }
    if (-not (Check-Uv)) { Install-Uv }

    Write-Host ""
    Info "Installing MachinaOS..."
    Write-Host ""

    # Install machinaos from npm
    npm install -g machinaos

    Write-Host ""
    Write-Color "============================================" "Green"
    Write-Color "  MachinaOS installed successfully!" "Green"
    Write-Color "============================================" "Green"
    Write-Host ""
    Write-Host "  Start MachinaOS:"
    Write-Host "    machinaos start"
    Write-Host ""
    Write-Host "  Open in browser:"
    Write-Host "    http://localhost:3000"
    Write-Host ""
}

# Run main
Main
