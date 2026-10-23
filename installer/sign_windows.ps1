<#
.SYNOPSIS
    Universal Scene Forge Windows Authenticode 签名脚本。

.DESCRIPTION
    对绿色版主程序与 NSIS 安装包执行 SHA-256 Authenticode 签名，并校验签名状态。
    支持两种证书来源：
      1) PFX 文件（推荐 CI/离线签名）；
      2) 已安装到证书存储的证书指纹（EV 硬件令牌通常用这种方式）。
    优先使用 Windows SDK 的 signtool.exe；找不到时回退到 PowerShell 内置
    Set-AuthenticodeSignature。

.EXAMPLE
    # 先干跑，确认目标文件与可用签名方式（不需要证书）
    powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 -DryRun

.EXAMPLE
    # PFX 签名（密码交互输入，不写入命令行/日志）
    powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 `
        -PfxPath D:\certs\usf-codesign.pfx

.EXAMPLE
    # 使用证书存储中的指纹（EV 令牌/USB Key）
    powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 `
        -Thumbprint 0123456789ABCDEF0123456789ABCDEF01234567

.EXAMPLE
    # 连同 onedir 内的 DLL 一并签名（包体较大时耗时明显）
    powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 `
        -PfxPath D:\certs\usf-codesign.pfx -AllBinaries
#>
[CmdletBinding()]
param(
    [string]$PfxPath,
    [System.Security.SecureString]$PfxPassword,
    [string]$Thumbprint,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$AllBinaries,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ProductName = "Universal Scene Forge"

$targets = @(
    (Join-Path $Root "dist\UniversalSceneForge\UniversalSceneForge.exe")
)
# 自动选最新的安装包（版本号变化时无需改脚本）
$setup = Get-ChildItem (Join-Path $Root "installer") `
    -Filter "UniversalSceneForge_*_Setup.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($setup) { $targets += $setup.FullName }
if ($AllBinaries) {
    $binRoot = Join-Path $Root "dist\UniversalSceneForge"
    if (Test-Path $binRoot) {
        $targets += Get-ChildItem $binRoot -Recurse -File |
            Where-Object { $_.Extension -in ".exe", ".dll" } |
            ForEach-Object { $_.FullName }
    }
}
$targets = $targets | Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -Unique
if (-not $targets) {
    throw "未找到可签名文件。请先运行 PyInstaller 与 NSIS 构建。"
}

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @(
        (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
        (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $hit = Get-ChildItem $root -Recurse -Filter signtool.exe `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

$signTool = Find-SignTool
$method = if ($signTool) { "signtool.exe" } else { "Set-AuthenticodeSignature (内置回退)" }

if ($DryRun) {
    Write-Host "[DRY-RUN] 产品: $ProductName"
    Write-Host "[DRY-RUN] 签名方式: $method"
    if ($signTool) { Write-Host "[DRY-RUN] signtool: $signTool" }
    Write-Host "[DRY-RUN] 时间戳: $TimestampUrl"
    foreach ($file in $targets) {
        Write-Host ("[DRY-RUN] 目标: {0} ({1:N0} 字节)" -f $file, (Get-Item $file).Length)
    }
    Write-Host "[DRY-RUN] 未提供证书，未执行签名。"
    exit 0
}

if (-not $PfxPath -and -not $Thumbprint) {
    throw "请提供 -PfxPath 或 -Thumbprint（可先加 -DryRun 检查目标）。"
}

$cert = $null
$plainPassword = $null
try {
    if ($PfxPath) {
        if (-not (Test-Path -LiteralPath $PfxPath)) {
            throw "PFX 不存在: $PfxPath"
        }
        if (-not $PfxPassword) {
            $PfxPassword = Read-Host -AsSecureString "PFX 密码"
        }
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PfxPassword)
        try {
            $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
        $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
            (Resolve-Path -LiteralPath $PfxPath).Path, $plainPassword, $flags)
    } else {
        $normalized = ($Thumbprint -replace "\s", "").ToUpperInvariant()
        $cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My |
            Where-Object { $_.Thumbprint -eq $normalized } |
            Select-Object -First 1
        if (-not $cert) {
            throw "证书存储中找不到指纹: $normalized"
        }
    }
    if (-not $cert.HasPrivateKey) {
        throw "所选证书没有私钥，无法签名: $($cert.Subject)"
    }
    Write-Host "[CERT] $($cert.Subject)"
    Write-Host "[CERT] 指纹: $($cert.Thumbprint)"

    foreach ($file in $targets) {
        Write-Host "[SIGN] $file"
        if ($signTool) {
            $args = @(
                "sign", "/fd", "SHA256", "/td", "SHA256",
                "/tr", $TimestampUrl, "/d", $ProductName
            )
            if ($PfxPath) {
                $args += @("/f", (Resolve-Path -LiteralPath $PfxPath).Path,
                           "/p", $plainPassword)
            } else {
                $args += @("/sha1", $cert.Thumbprint)
            }
            $args += $file
            & $signTool @args
            if ($LASTEXITCODE -ne 0) {
                throw "signtool 签名失败 (exit $LASTEXITCODE): $file"
            }
        } else {
            $result = Set-AuthenticodeSignature -FilePath $file `
                -Certificate $cert -HashAlgorithm SHA256 `
                -TimestampServer $TimestampUrl
            if ($result.Status -ne "Valid") {
                throw "Set-AuthenticodeSignature 失败: $($result.Status) $($result.StatusMessage)"
            }
        }

        $sig = Get-AuthenticodeSignature -FilePath $file
        if ($sig.Status -ne "Valid") {
            throw "签名校验失败: $file -> $($sig.Status) $($sig.StatusMessage)"
        }
        $stamp = if ($sig.TimeStamperCertificate) {
            $sig.TimeStamperCertificate.NotAfter.ToString("u")
        } else {
            "无时间戳证书（请检查 TimestampUrl）"
        }
        Write-Host "[OK] $file"
        Write-Host "     签名者: $($sig.SignerCertificate.Subject)"
        Write-Host "     时间戳: $stamp"
    }
} finally {
    if ($plainPassword) { $plainPassword = $null }
    if ($cert) { $cert.Dispose() }
}

Write-Host "全部签名并校验通过。ZIP 为压缩包，Authenticode 只适用于 PE 文件（exe/dll）。"
