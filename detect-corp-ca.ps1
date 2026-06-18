# detect-corp-ca.ps1 — detect a corporate TLS-intercepting (MITM) proxy and STAGE its
# root CA for opt-in trust (Windows). It never enables trust by itself: review the
# printed SHA-256 fingerprint with your IT department, then set CORP_CA_AUTO_TRUST=1 in .env.
#
# Usage: .\detect-corp-ca.ps1 [-ProbeHost huggingface.co] [-Port 443]
param(
    [string]$ProbeHost = "huggingface.co",
    [int]$Port = 443
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$dest = Join-Path $PSScriptRoot "certs\extra-ca"
New-Item -ItemType Directory -Force $dest | Out-Null

$script:chainRoot = $null
try {
    $tcp = [System.Net.Sockets.TcpClient]::new($ProbeHost, $Port)
    $cb = [System.Net.Security.RemoteCertificateValidationCallback] {
        param($sender, $cert, $chain, $errors)
        if ($chain -and $chain.ChainElements.Count -gt 0) {
            $script:chainRoot = $chain.ChainElements[$chain.ChainElements.Count - 1].Certificate
        }
        return $true   # accept for INSPECTION only; we transmit no secrets
    }
    $ssl = [System.Net.Security.SslStream]::new($tcp.GetStream(), $false, $cb)
    $ssl.AuthenticateAsClient($ProbeHost)
    $ssl.Dispose(); $tcp.Close()
} catch {
    Write-Error "Could not establish a TLS connection to ${ProbeHost}:${Port}: $($_.Exception.Message)"
    exit 1
}

if (-not $script:chainRoot) {
    Write-Error "No certificate chain captured from ${ProbeHost}:${Port}."
    exit 1
}

$root = $script:chainRoot
$publicRe = "DigiCert|Baltimore|ISRG|Let'?s Encrypt|Global ?Sign|Sectigo|USERTrust|Amazon|Google Trust|Microsoft .*Root|Entrust|GoDaddy|Comodo|Starfield|Certum|Thawte|VeriSign"
if ($root.Subject -match $publicRe) {
    Write-Host "Top-of-chain root looks public:"
    Write-Host "  $($root.Subject)"
    Write-Host "No corporate interception detected; nothing staged."
    exit 0
}

$fp = $root.GetCertHashString("SHA256")
$pem = "-----BEGIN CERTIFICATE-----`n" +
    ([Convert]::ToBase64String($root.RawData, 'InsertLineBreaks')) +
    "`n-----END CERTIFICATE-----`n"
$out = Join-Path $dest ("corp-root-" + $fp.Substring(0, 16) + ".crt")
Set-Content -Path $out -Value $pem -Encoding ascii

Write-Host ""
Write-Host "Corporate root staged: $out"
Write-Host "  subject: $($root.Subject)"
Write-Host "  SHA-256: $fp"
Write-Host ""
Write-Host "NEXT: VERIFY this SHA-256 with your IT department, then set CORP_CA_AUTO_TRUST=1 in .env"
Write-Host "      and rebuild/start (.\build-gigaam.ps1 then .\start.ps1). Verification stays ON - the CA is ADDED, not bypassed."
