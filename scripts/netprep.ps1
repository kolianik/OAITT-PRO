# netprep.ps1 — shared network preflight for the OAITT-PRO start/build scripts (Windows).
#
# DOT-SOURCE this file (do not run as a child) so it mutates the parent session env:
#   . "$PSScriptRoot/scripts/netprep.ps1"
#
# Mirrors scripts/netprep.sh: discover system proxy (env + WinINET registry + WinHTTP) ->
# probe DIRECT reachability with the proxy bypassed -> if direct works, DISABLE proxying;
# else translate a loopback proxy to http://host.docker.internal:<port> or pass a
# non-loopback proxy through. Exports HTTP_PROXY/HTTPS_PROXY (+lowercase), NO_PROXY
# (+lowercase), OAITT_PROXY_MODE. Set $env:OAITT_NETPREP_NO_AUTORUN=1 to skip the pipeline.

function Write-NetprepLog($msg) { Write-Host "[netprep] $msg" }

function ConvertFrom-WinINetProxy([string]$raw) {
    # "host:port"  OR  "http=h:p;https=h:p;ftp=...;socks=..." — prefer https, then http.
    if ($raw -match '=') {
        $map = @{}
        foreach ($pair in $raw -split ';') {
            $kv = $pair -split '=', 2
            if ($kv.Count -eq 2) { $map[$kv[0].Trim().ToLower()] = $kv[1].Trim() }
        }
        $val = if ($map.ContainsKey('https')) { $map['https'] } elseif ($map.ContainsKey('http')) { $map['http'] } else { $null }
    } else {
        $val = $raw.Trim()
    }
    if (-not $val) { return $null }
    if ($val -notmatch '^\w+://') { $val = "http://$val" }
    return $val
}

function Convert-NetprepLoopback([string]$url) {
    if ($url -match '127\.0\.0\.1|localhost|::1') {
        $hostport = $url -replace '^[^:]+://', ''
        $hostport = $hostport -replace '/.*$', ''
        $port = 80
        if ($hostport -match '\]:(\d+)$') { $port = $Matches[1] }
        elseif ($hostport -match ':(\d+)$') { $port = $Matches[1] }
        return "http://host.docker.internal:$port"
    }
    return $url
}

function Get-NetprepNoProxy {
    $base = 'localhost,127.0.0.1,::1,host.docker.internal,gateway-orchestrator,whisperx-service,gigaam-service,postgres,front-proxy,certbot,.local,.internal'
    if ($env:OAITT_PROXY_BYPASS) { return "$base,$($env:OAITT_PROXY_BYPASS)" }
    return $base
}

function Get-NetprepProxy {
    foreach ($v in 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy') {
        $val = [Environment]::GetEnvironmentVariable($v)
        if ($val) { return $val }
    }
    try {
        $k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        $ie = Get-ItemProperty -Path $k -ErrorAction SilentlyContinue
        if ($ie -and $ie.ProxyEnable -eq 1 -and $ie.ProxyServer) {
            if ($ie.ProxyOverride) { $env:OAITT_PROXY_BYPASS = ($ie.ProxyOverride -replace '<local>', '.local') }
            return (ConvertFrom-WinINetProxy $ie.ProxyServer)
        }
    } catch {}
    try {
        $wh = (netsh winhttp show proxy 2>$null | Out-String)
        if ($wh -match 'Proxy Server\(s\)\s*:\s*([^\r\n]+)') {
            $srv = $Matches[1].Trim()
            if ($srv -and $srv -ne '(none)') { return (ConvertFrom-WinINetProxy $srv) }
        }
    } catch {}
    return $null
}

function Test-NetprepReach([string]$targetHost, [int]$port = 443) {
    try {
        $c = [System.Net.Sockets.TcpClient]::new()
        $iar = $c.BeginConnect($targetHost, $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(3000, $false)
        if ($ok) { $c.EndConnect($iar) }
        $c.Close()
        return $ok
    } catch { return $false }
}

function Test-NetprepDirect {
    $all = $true
    foreach ($h in 'registry-1.docker.io', 'pypi.org', 'huggingface.co') {
        if (-not (Test-NetprepReach $h 443)) { $all = $false }
    }
    return $all
}

function Test-NetprepProxy([string]$proxy) {
    try {
        Invoke-WebRequest -Proxy $proxy -Uri 'https://pypi.org' -Method Head -TimeoutSec 5 -UseBasicParsing | Out-Null
        return $true
    } catch { return $false }
}

function Invoke-NetprepMain {
    $proxy = Get-NetprepProxy
    if (Test-NetprepDirect) {
        $mode = 'direct'; $resolved = ''
        if ($proxy) { Write-NetprepLog "Direct internet reachable; ignoring configured proxy ($proxy)." }
        else { Write-NetprepLog 'Direct internet reachable; no proxy needed.' }
    }
    elseif ($proxy -and (Test-NetprepProxy $proxy)) {
        $resolved = Convert-NetprepLoopback $proxy
        if ($proxy -match '127\.0\.0\.1|localhost|::1') { $mode = 'translated' } else { $mode = 'passthrough' }
        Write-NetprepLog "Direct blocked; using proxy ($mode): $resolved"
    }
    else {
        $mode = 'none'; $resolved = ''
        Write-NetprepLog 'WARNING: no direct internet and no working proxy. Builds/bootstrap will fail unless the cache is seeded (INSTALL.md, GIGAAM_OFFLINE_MODE).'
    }

    $env:HTTP_PROXY = $resolved; $env:HTTPS_PROXY = $resolved
    $env:http_proxy = $resolved; $env:https_proxy = $resolved
    $np = Get-NetprepNoProxy
    $env:NO_PROXY = $np; $env:no_proxy = $np
    $env:OAITT_PROXY_MODE = $mode

    if ($mode -ne 'direct') {
        $dproxy = (docker info --format '{{.HTTPProxy}}' 2>$null)
        if (-not $dproxy) {
            Write-NetprepLog 'Note: the Docker daemon has no proxy configured - base-image pulls may fail. Set Docker Desktop Settings -> Resources -> Proxies (see INSTALL.md section 6).'
        }
    }
}

if (-not $env:OAITT_NETPREP_NO_AUTORUN) { Invoke-NetprepMain }
