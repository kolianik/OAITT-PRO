# Build gigaam-service image (deps only; models bootstrap on first container start).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:DOCKER_BUILDKIT = "1"

# Daemon preflight: fail fast with a clear message instead of a cryptic HTTP 500.
$_server = docker version --format '{{.Server.APIVersion}}' 2>$null
if (-not $_server) {
    Write-Error "Docker daemon is not reachable. Quit Docker Desktop, run 'wsl --shutdown', restart Docker Desktop, wait for 'Engine running', then re-run. See TROUBLESHOOTING.md -> Error 1b."
    exit 1
}
# Pin DOCKER_API_VERSION only when CLI is strictly newer than the server (currently both = 1.54).
$_client = docker version --format '{{.Client.APIVersion}}' 2>$null
if ($_client -and ([version]$_client -gt [version]$_server)) {
    $env:DOCKER_API_VERSION = $_server
    Write-Host "CLI api $_client > server api $_server; pinned DOCKER_API_VERSION=$_server"
}

# Resolve the proxy contract (discover incl. WinINET/WinHTTP -> probe direct -> disable /
# translate loopback->host.docker.internal / passthrough) and export it for BuildKit build-args.
. "$PSScriptRoot/scripts/netprep.ps1"

# Corporate MITM CA (Scenario S3): build-arg gate. Trusted only when staged AND opted in.
if (($env:CORP_CA_AUTO_TRUST -eq "1") -and (Get-ChildItem "$PSScriptRoot/certs/extra-ca/*.crt" -ErrorAction SilentlyContinue)) {
    $env:CORP_CA_AUTO_TRUST = "1"
    Write-Host "CORP_CA_AUTO_TRUST=1: corporate CA(s) in certs/extra-ca will be trusted at build time."
} else {
    if (-not $env:CORP_CA_AUTO_TRUST) { $env:CORP_CA_AUTO_TRUST = "0" }
}

docker compose build @args gigaam-service
