# 一键 HTTPS 启动（Windows / PowerShell）
# 生成本机自签证书，并用 TLS 启动私人数据时光轴。
#
# 用法:
#   .\deploy\start_https.ps1
#   .\deploy\start_https.ps1 -Port 8765 -Hosts "localhost,127.0.0.1,timeline.local"
#   .\deploy\start_https.ps1 -UseCaddy          # 若已安装 Caddy，用反代 + 自动 HTTPS
#   .\deploy\start_https.ps1 -Domain timeline.example.com -UseCaddy
#
# 仅在本项目目录写入文件；不修改全局防火墙（如需外网请自行放行端口）。

param(
    [int]$Port = 8764,
    [string]$Hosts = "localhost,127.0.0.1",
    [string]$Domain = "",
    [switch]$UseCaddy,
    [int]$CaddyHttpsPort = 8443
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Python {
    if ($env:MIMO_PYTHON -and (Test-Path $env:MIMO_PYTHON)) { return $env:MIMO_PYTHON }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
    throw "未找到 Python"
}

function Find-Caddy {
    $c = Get-Command caddy -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $local = Join-Path $Root "deploy\caddy.exe"
    if (Test-Path $local) { return $local }
    return $null
}

# ---------- Caddy 路径 ----------
if ($UseCaddy) {
    $caddy = Find-Caddy
    if (-not $caddy) {
        Write-Host "[错误] 未找到 Caddy。可从 https://caddyserver.com/download 下载 caddy.exe 放到 deploy\caddy.exe" -ForegroundColor Red
        Write-Host "或去掉 -UseCaddy，使用自签证书直连。" -ForegroundColor Yellow
        exit 1
    }
    $cfg = Join-Path $Root "deploy\Caddyfile.local"
    if ($Domain) {
        @"
$Domain {
    encode gzip
    reverse_proxy 127.0.0.1:$Port
}
"@ | Set-Content -Path $cfg -Encoding UTF8
    } else {
        @"
:$CaddyHttpsPort {
    tls internal
    reverse_proxy 127.0.0.1:$Port
}
"@ | Set-Content -Path $cfg -Encoding UTF8
    }
    Write-Host "=== 启动应用（HTTP 后端 $Port）===" -ForegroundColor Green
    $py = Find-Python
    Start-Process -FilePath $py -ArgumentList "server.py","--host","127.0.0.1","--port","$Port" -WorkingDirectory $Root -WindowStyle Hidden
    Start-Sleep -Seconds 1
    Write-Host "=== 启动 Caddy HTTPS ===" -ForegroundColor Green
    if ($Domain) {
        Write-Host "访问: https://$Domain" -ForegroundColor Cyan
    } else {
        Write-Host "访问: https://localhost:$CaddyHttpsPort  （tls internal 自签）" -ForegroundColor Cyan
    }
    & $caddy run --config $cfg
    exit 0
}

# ---------- 自签证书 + 应用内 TLS ----------
$CertDir = Join-Path $Root "deploy\certs"
New-Item -ItemType Directory -Force -Path $CertDir | Out-Null
$certPem = Join-Path $CertDir "tls.crt"
$keyPem  = Join-Path $CertDir "tls.key"

if ((Test-Path $certPem) -and (Test-Path $keyPem)) {
    Write-Host "复用已有证书: $certPem" -ForegroundColor Yellow
} else {
    Write-Host "生成自签证书 ($Hosts)..." -ForegroundColor Green
    $dns = @($Hosts -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($Domain -and $dns -notcontains $Domain) { $dns += $Domain }

    $openssl = Get-Command openssl -ErrorAction SilentlyContinue
    if ($openssl) {
        # 首选 openssl（跨平台行为一致）
        $cnf = Join-Path $CertDir "openssl.cnf"
        $sanLines = @()
        $i = 1
        foreach ($h in $dns) {
            if ($h -match '^[0-9.]+$') { $sanLines += "IP.$i = $h" } else { $sanLines += "DNS.$i = $h" }
            $i++
        }
        @"
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no
[dn]
CN = personal-timeline
[v3_req]
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[alt_names]
$($sanLines -join "`n")
"@ | Set-Content -Path $cnf -Encoding ASCII
        & $openssl.Source req -x509 -nodes -newkey rsa:2048 `
            -keyout $keyPem -out $certPem -days 730 -config $cnf
        if ($LASTEXITCODE -ne 0) { throw "openssl 生成证书失败" }
    } else {
        # 回退：.NET 证书 + ExportParameters 编码 PKCS#1 PEM
        $cert = New-SelfSignedCertificate `
            -DnsName $dns `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -KeyExportPolicy Exportable `
            -KeyLength 2048 `
            -HashAlgorithm SHA256 `
            -NotAfter (Get-Date).AddYears(2) `
            -Provider "Microsoft Enhanced RSA and AES Cryptographic Provider"

        $certBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
        $certB64 = [Convert]::ToBase64String($certBytes, [System.Base64FormattingOptions]::InsertLineBreaks)
        @"
-----BEGIN CERTIFICATE-----
$certB64
-----END CERTIFICATE-----
"@ | Set-Content -Path $certPem -Encoding ASCII

        $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
        if (-not $rsa) { throw "无法获取私钥" }
        $p = $rsa.ExportParameters($true)

        function Encode-AsnInt([byte[]]$Value) {
            # strip leading zeros
            $list = [System.Collections.Generic.List[byte]]::new()
            $list.AddRange($Value)
            while ($list.Count -gt 1 -and $list[0] -eq 0) { $list.RemoveAt(0) }
            if ($list[0] -ge 0x80) { $list.Insert(0, 0) }
            $body = $list.ToArray()
            $out = [System.Collections.Generic.List[byte]]::new()
            $out.Add(0x02)
            if ($body.Length -lt 0x80) { $out.Add([byte]$body.Length) }
            else {
                $len = $body.Length
                $lb = [System.Collections.Generic.List[byte]]::new()
                while ($len -gt 0) { $lb.Insert(0, [byte]($len -band 0xFF)); $len = $len -shr 8 }
                $out.Add([byte](0x80 -bor $lb.Count)); $out.AddRange($lb)
            }
            $out.AddRange($body)
            return $out.ToArray()
        }
        function Encode-AsnSeq([byte[][]]$Parts) {
            $body = [System.Collections.Generic.List[byte]]::new()
            foreach ($part in $Parts) { $body.AddRange($part) }
            $out = [System.Collections.Generic.List[byte]]::new()
            $out.Add(0x30)
            $len = $body.Count
            if ($len -lt 0x80) { $out.Add([byte]$len) }
            else {
                $lb = [System.Collections.Generic.List[byte]]::new()
                while ($len -gt 0) { $lb.Insert(0, [byte]($len -band 0xFF)); $len = $len -shr 8 }
                $out.Add([byte](0x80 -bor $lb.Count)); $out.AddRange($lb)
            }
            $out.AddRange($body)
            return $out.ToArray()
        }

        $zero = Encode-AsnInt ([byte[]]@(0))
        $parts = @(
            $zero,  # version 0
            (Encode-AsnInt $p.Modulus),
            (Encode-AsnInt $p.Exponent),
            (Encode-AsnInt $p.D),
            (Encode-AsnInt $p.P),
            (Encode-AsnInt $p.Q),
            (Encode-AsnInt $p.DP),
            (Encode-AsnInt $p.DQ),
            (Encode-AsnInt $p.InverseQ)
        )
        $der = Encode-AsnSeq $parts
        $keyB64 = [Convert]::ToBase64String($der, [System.Base64FormattingOptions]::InsertLineBreaks)
        @"
-----BEGIN RSA PRIVATE KEY-----
$keyB64
-----END RSA PRIVATE KEY-----
"@ | Set-Content -Path $keyPem -Encoding ASCII
    }
    Write-Host "证书已写入 $CertDir" -ForegroundColor Green
}

$py = Find-Python
Write-Host "=== 启动 HTTPS 服务 ===" -ForegroundColor Green
Write-Host "访问: https://localhost:$Port" -ForegroundColor Cyan
Write-Host "浏览器会提示自签证书告警，点「继续访问」即可（家人首次也一样）。" -ForegroundColor Yellow
Write-Host "生产域名请改用 -UseCaddy -Domain your.domain 以获得可信证书。" -ForegroundColor Yellow
& $py server.py --host 0.0.0.0 --port $Port --https --cert $certPem --key $keyPem
