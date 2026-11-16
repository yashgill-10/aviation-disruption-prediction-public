$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repoRoot '.env'

$secureKey = Read-Host 'Enter your Aviationstack API key (input is hidden)' -AsSecureString
if ($secureKey.Length -eq 0) {
    throw 'No key was entered. The local configuration was not changed.'
}

$keyPointer = [IntPtr]::Zero
$keyValue = $null
$tempPath = "$envPath.$([guid]::NewGuid().ToString('N')).tmp"
try {
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $keyValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    if ($keyValue.Contains("`r") -or $keyValue.Contains("`n")) {
        throw 'The key contains an unsupported line break. The local configuration was not changed.'
    }

    $lines = @()
    if (Test-Path -LiteralPath $envPath) {
        $lines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    }
    $lines = @($lines | Where-Object {
        $_ -notmatch '^\s*FLIGHT_PROVIDER\s*=' -and $_ -notmatch '^\s*FLIGHT_API_KEY\s*='
    })
    $lines += 'FLIGHT_PROVIDER=aviationstack'
    $lines += "FLIGHT_API_KEY=$keyValue"

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines($tempPath, [string[]]$lines, $utf8NoBom)
    Move-Item -LiteralPath $tempPath -Destination $envPath -Force

    Write-Output 'Saved Aviationstack configuration to the git-ignored .env file. The key value was not displayed.'
}
finally {
    if (Test-Path -LiteralPath $tempPath) {
        Remove-Item -LiteralPath $tempPath -Force
    }
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    if ($secureKey) {
        $secureKey.Dispose()
    }
    $keyValue = $null
}
