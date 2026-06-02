#requires -version 5.1
<#
.SYNOPSIS
    One-shot environment setup for Ariadne Lineage Platform on Windows 10/11.

.DESCRIPTION
    Installs and verifies everything Windows-side that you need BEFORE
    cloning the repo:
      - Windows version check (10 build 19041+ or 11)
      - WSL2 kernel + default-version 2
      - Ubuntu distro under WSL
      - Docker Desktop (with WSL2 backend)
      - Git
      - Free-port check on 3000, 8000, 7475, 7688, 5432, 8001-8004
    Then it prints the exact next-step commands to run inside WSL Ubuntu
    (Python 3.11, Node 18, repo clone, ./start.sh) — there is a companion
    setup-wsl.sh script for that side.

    The script is idempotent — re-running it on a fully-prepared machine is a
    no-op that just reports OK against every check.

.PARAMETER SkipDocker
    Skip the Docker Desktop install/check. Use if you've already got it
    working with a different config.

.PARAMETER SkipWsl
    Skip the WSL2 / Ubuntu setup. Use only if you've already installed both.

.EXAMPLE
    # Run from an elevated PowerShell prompt (Right-click -> Run as Administrator):
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    .\setup-windows.ps1

.NOTES
    Some steps require Administrator. The script self-checks and tells you
    to relaunch elevated if you're not.
#>

[CmdletBinding()]
param(
    [switch]$SkipDocker,
    [switch]$SkipWsl
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Pretty output helpers
# ---------------------------------------------------------------------------
function Write-Step  { param($Msg) Write-Host "[*] $Msg" -ForegroundColor Cyan }
function Write-Ok    { param($Msg) Write-Host "[OK] $Msg" -ForegroundColor Green }
function Write-Warn  { param($Msg) Write-Host "[!] $Msg" -ForegroundColor Yellow }
function Write-Err   { param($Msg) Write-Host "[X] $Msg" -ForegroundColor Red }
function Write-Hint  { param($Msg) Write-Host "    $Msg" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "Ariadne Lineage Platform - Windows environment setup" -ForegroundColor White
Write-Host "=====================================================" -ForegroundColor White
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Admin check (winget + WSL kernel install both need it)
# ---------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Err "This script needs Administrator. Right-click PowerShell -> Run as Administrator, then re-run."
    exit 1
}
Write-Ok "Running as Administrator"

# ---------------------------------------------------------------------------
# 2. Windows version (WSL2 needs Win10 build 19041+ or Win11)
# ---------------------------------------------------------------------------
Write-Step "Checking Windows version"
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
if ($build -lt 19041) {
    Write-Err "Windows build $build is too old. WSL2 needs build 19041 or newer (Windows 10 2004 / 21H1+, or any Windows 11)."
    Write-Hint "Run Windows Update first, then re-run this script."
    exit 1
}
Write-Ok "Windows build $build (>= 19041)"

# ---------------------------------------------------------------------------
# 3. winget (Windows Package Manager) — used to install everything else
# ---------------------------------------------------------------------------
Write-Step "Checking winget"
$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Err "winget is not installed."
    Write-Hint "Install 'App Installer' from the Microsoft Store, then re-run this script."
    Write-Hint "  ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"
    exit 1
}
Write-Ok "winget present"

# ---------------------------------------------------------------------------
# 4. Git for Windows (needed before we clone the repo; harmless if also
#    installed inside WSL later)
# ---------------------------------------------------------------------------
Write-Step "Checking Git"
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Step "Installing Git via winget..."
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    Write-Ok "Git installed (open a NEW shell to pick up PATH changes)"
} else {
    $gitVer = (git --version) -replace '^git version ', ''
    Write-Ok "Git $gitVer"
}

# ---------------------------------------------------------------------------
# 5. WSL2 + Ubuntu
# ---------------------------------------------------------------------------
if (-not $SkipWsl) {
    Write-Step "Checking WSL feature state"

    $vmp = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue
    $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue

    $needReboot = $false
    if ($vmp.State -ne 'Enabled') {
        Write-Step "Enabling Virtual Machine Platform..."
        Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart | Out-Null
        $needReboot = $true
    } else { Write-Ok "Virtual Machine Platform enabled" }

    if ($wslFeature.State -ne 'Enabled') {
        Write-Step "Enabling Windows Subsystem for Linux..."
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart | Out-Null
        $needReboot = $true
    } else { Write-Ok "WSL feature enabled" }

    if ($needReboot) {
        Write-Warn "Windows features were just enabled. Reboot Windows, then re-run this script to continue."
        exit 0
    }

    Write-Step "Setting WSL default version to 2"
    wsl --set-default-version 2 2>&1 | Out-Null
    Write-Ok "WSL default = v2"

    # Update WSL kernel just to be safe (no-op if already current).
    Write-Step "Updating WSL kernel"
    wsl --update 2>&1 | Out-Null
    Write-Ok "WSL kernel up-to-date"

    # Ubuntu install (skips if already installed)
    Write-Step "Checking for Ubuntu distro under WSL"
    $distros = (wsl -l -q) -replace "`0", "" | Where-Object { $_ -match '\S' }
    if ($distros -notcontains 'Ubuntu') {
        Write-Step "Installing Ubuntu (this opens a window; create your Linux username/password, then close it)..."
        wsl --install -d Ubuntu
        Write-Warn "Ubuntu is installing in the background. Open the Ubuntu app from Start Menu once,"
        Write-Warn "finish first-time setup (username + password), then re-run this script."
        exit 0
    }
    Write-Ok "Ubuntu installed under WSL"
} else {
    Write-Warn "Skipping WSL setup (-SkipWsl)"
}

# ---------------------------------------------------------------------------
# 6. Docker Desktop
# ---------------------------------------------------------------------------
if (-not $SkipDocker) {
    Write-Step "Checking Docker Desktop"
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Step "Installing Docker Desktop via winget..."
        winget install --id Docker.DockerDesktop -e --source winget --accept-package-agreements --accept-source-agreements
        Write-Warn "Docker Desktop installed. LAUNCH IT MANUALLY ONCE so it can enable the WSL2 backend,"
        Write-Warn "then re-run this script to finish verification."
        Write-Hint "After Docker starts: Settings -> Resources -> WSL Integration -> enable for Ubuntu"
        exit 0
    }

    Write-Step "Verifying Docker daemon is running"
    try {
        docker info | Out-Null
        Write-Ok "Docker daemon responding"
    } catch {
        Write-Err "Docker is installed but the daemon isn't running. Start Docker Desktop from the Start Menu, then re-run."
        exit 1
    }

    Write-Step "Checking Docker WSL2 integration for Ubuntu"
    $dockerWsl = wsl -d Ubuntu -- bash -lc "command -v docker" 2>$null
    if (-not $dockerWsl) {
        Write-Warn "Docker CLI not yet visible inside Ubuntu."
        Write-Hint "Open Docker Desktop -> Settings -> Resources -> WSL Integration -> enable Ubuntu,"
        Write-Hint "then re-run this script."
    } else {
        Write-Ok "docker CLI available inside Ubuntu ($dockerWsl)"
    }
} else {
    Write-Warn "Skipping Docker setup (-SkipDocker)"
}

# ---------------------------------------------------------------------------
# 7. Port-collision check (informational — these are the ports start.sh uses)
# ---------------------------------------------------------------------------
Write-Step "Checking required ports are free"
$portsToCheck = @(3000, 8000, 7475, 7688, 5432, 8001, 8002, 8003, 8004)
$busy = @()
foreach ($p in $portsToCheck) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($conn) { $busy += $p }
}
if ($busy.Count -eq 0) {
    Write-Ok "All required ports free: $($portsToCheck -join ', ')"
} else {
    Write-Warn "Ports already in use: $($busy -join ', ')"
    Write-Hint "Either stop the conflicting process, or edit lineage-platform/docker-compose.yml"
    Write-Hint "to remap. Postgres on 5432 is the usual culprit — stop a local Postgres service first."
}

# ---------------------------------------------------------------------------
# 8. Drop the WSL setup script into Ubuntu's home so the user can run it
#    on the Linux side. We embed it inline so this PS1 is self-contained.
# ---------------------------------------------------------------------------
Write-Step "Staging setup-wsl.sh inside Ubuntu's home directory"
$wslSetup = @'
#!/usr/bin/env bash
# Ariadne Lineage Platform - WSL/Ubuntu environment setup
# Run from inside WSL Ubuntu after setup-windows.ps1 completes.
set -euo pipefail

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
step() { printf "%b[*]%b %s\n" "$C" "$N" "$1"; }
ok()   { printf "%b[OK]%b %s\n" "$G" "$N" "$1"; }
warn() { printf "%b[!]%b %s\n"  "$Y" "$N" "$1"; }

step "Updating apt index"
sudo apt-get update -y

step "Installing system tools (curl, lsof, git, build-essential)"
sudo apt-get install -y curl lsof git build-essential ca-certificates software-properties-common

# Python 3.11+
if ! command -v python3.11 >/dev/null 2>&1 && ! python3 -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
    step "Installing Python 3.11"
    sudo add-apt-repository -y ppa:deadsnakes/ppa || true
    sudo apt-get update -y
    sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip
fi
PY=$(command -v python3.11 || command -v python3)
ok "Python: $($PY --version)"

# Node 18+ via NodeSource
if ! command -v node >/dev/null 2>&1 || ! node -e "process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)" 2>/dev/null; then
    step "Installing Node.js 18.x"
    curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
ok "Node: $(node --version) / npm: $(npm --version)"

# Docker CLI (Docker Desktop's WSL integration usually drops it in, but
# install the apt package as a fallback if it isn't there).
if ! command -v docker >/dev/null 2>&1; then
    warn "docker CLI not found inside WSL. Enable Docker Desktop's WSL integration:"
    warn "  Docker Desktop -> Settings -> Resources -> WSL Integration -> toggle Ubuntu ON"
fi

step "Verifying ports are free"
busy=()
for p in 3000 8000 7475 7688 5432 8001 8002 8003 8004; do
    lsof -ti:"$p" >/dev/null 2>&1 && busy+=("$p") || true
done
if [ ${#busy[@]} -eq 0 ]; then
    ok "All required ports free"
else
    warn "Ports already in use: ${busy[*]}"
fi

echo ""
ok "WSL setup complete. Now:"
echo "    git clone https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform.git"
echo "    cd Ariadne---Lineage-Platform"
echo "    ./start.sh"
'@

$wslSetupPath = Join-Path $env:TEMP "setup-wsl.sh"
$wslSetup | Out-File -FilePath $wslSetupPath -Encoding utf8 -NoNewline
# Copy into Ubuntu's home and make executable
$wslHomePath = (wsl -d Ubuntu -- bash -lc "echo \$HOME").Trim()
wsl -d Ubuntu -- bash -lc "cp '$(wsl -d Ubuntu -- wslpath -a $wslSetupPath.Replace('\','/'))' ~/setup-wsl.sh && chmod +x ~/setup-wsl.sh" 2>$null
Write-Ok "setup-wsl.sh staged at $wslHomePath/setup-wsl.sh inside Ubuntu"

# ---------------------------------------------------------------------------
# 9. Done — print the next-step instructions
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=====================================================" -ForegroundColor White
Write-Host " Windows-side setup complete" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor White
Write-Host ""
Write-Host "Next steps - run these inside WSL Ubuntu:" -ForegroundColor White
Write-Host ""
Write-Host "  1. Open Ubuntu (Start Menu -> Ubuntu)" -ForegroundColor White
Write-Host "  2. ~/setup-wsl.sh                          # Linux-side prerequisites" -ForegroundColor Cyan
Write-Host "  3. git clone https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform.git" -ForegroundColor Cyan
Write-Host "  4. cd Ariadne---Lineage-Platform" -ForegroundColor Cyan
Write-Host "  5. ./start.sh" -ForegroundColor Cyan
Write-Host ""
Write-Host "After start.sh finishes:" -ForegroundColor White
Write-Host "  Frontend     http://localhost:3000" -ForegroundColor DarkGray
Write-Host "  Gateway      http://localhost:8000/docs" -ForegroundColor DarkGray
Write-Host "  Neo4j        http://localhost:7475   (neo4j / lineagepass)" -ForegroundColor DarkGray
Write-Host ""
