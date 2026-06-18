# Start all OAITT-PRO services. Use this instead of bare `docker compose up -d`.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Daemon preflight: fail fast with a clear message instead of a cryptic HTTP 500.
$_server = docker version --format '{{.Server.APIVersion}}' 2>$null
if (-not $_server) {
    Write-Error @"
Docker daemon is not reachable (no server API version returned).
The Docker Desktop Linux backend is likely crashed or still starting.
Fix:
  1. Quit Docker Desktop completely (tray -> Quit Docker Desktop).
  2. Run: wsl --shutdown
  3. Start Docker Desktop and wait for 'Engine running'.
  4. Re-run this script.
See TROUBLESHOOTING.md -> Error 1b.
"@
    exit 1
}
# Pin DOCKER_API_VERSION only when CLI is strictly newer than the server (currently both = 1.54).
$_client = docker version --format '{{.Client.APIVersion}}' 2>$null
if ($_client -and ([version]$_client -gt [version]$_server)) {
    $env:DOCKER_API_VERSION = $_server
    Write-Host "CLI api $_client > server api $_server; pinned DOCKER_API_VERSION=$_server"
}

# Resolve the proxy contract (discover system proxy incl. WinINET/WinHTTP -> probe direct
# reachability with the proxy bypassed -> disable / translate loopback->host.docker.internal
# / passthrough) and export it for Compose + BuildKit. Dot-sourced so it mutates this session.
. "$PSScriptRoot/scripts/netprep.ps1"

# Corporate MITM CA (Scenario S3): trusted only when a CA is staged AND opted in (fail-closed default).
if (($env:CORP_CA_AUTO_TRUST -eq "1") -and (Get-ChildItem "$PSScriptRoot/certs/extra-ca/*.crt" -ErrorAction SilentlyContinue)) {
    $env:CORP_CA_AUTO_TRUST = "1"
    Write-Host "CORP_CA_AUTO_TRUST=1: corporate CA(s) in certs/extra-ca will be trusted by builds + runtime."
    Write-Host "Docker daemon image pulls use the Windows Root store - ensure the corporate root is imported there (often via GPO)."
} else {
    if (-not $env:CORP_CA_AUTO_TRUST) { $env:CORP_CA_AUTO_TRUST = "0" }
}

$env:DOCKER_BUILDKIT = "1"
docker compose up -d @args
