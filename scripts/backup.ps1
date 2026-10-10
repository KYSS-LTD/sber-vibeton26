param([string]$Destination = '.\backups', [string]$Project = 'prohod')
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$backupPath = [IO.Path]::GetFullPath((Join-Path $workspacePath $Destination))
New-Item -ItemType Directory -Force -Path $backupPath | Out-Null
function DockerStep([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker step failed: $($Arguments[0])" }
}
Push-Location $workspacePath
try {
    DockerStep @('compose','-p',$Project,'stop','backend','worker','storage-worker','frontend','keycloak')
    DockerStep @('compose','-p',$Project,'exec','-T','postgres','sh','-c','PGPASSWORD=$(cat /bootstrap/db_owner) pg_dump -U prohod_owner -d prohod -Fc -f /tmp/prohod.dump')
    DockerStep @('compose','-p',$Project,'exec','-T','postgres','sh','-c','PGPASSWORD=$(cat /bootstrap/db_owner) pg_dump -U prohod_owner -d keycloak -Fc -f /tmp/keycloak.dump')
    DockerStep @('compose','-p',$Project,'cp','postgres:/tmp/prohod.dump',(Join-Path $backupPath 'prohod.dump'))
    DockerStep @('compose','-p',$Project,'cp','postgres:/tmp/keycloak.dump',(Join-Path $backupPath 'keycloak.dump'))
    DockerStep @('compose','-p',$Project,'stop','s3')
    foreach ($volumeKind in @('objects','bootstrap_secrets','runtime_secrets','certificates')) {
        DockerStep @('run','--rm','--mount',"type=volume,source=${Project}_${volumeKind},target=/source,readonly",'--mount',"type=bind,source=$backupPath,target=/backup",'alpine:3.22.0','tar','czf',"/backup/$volumeKind.tar.gz",'-C','/source','.')
    }
    @{project=$Project; created_at=[DateTime]::UtcNow.ToString('o'); format=1} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupPath 'manifest.json') -Encoding utf8
} finally {
    & docker compose -p $Project start postgres s3 keycloak backend worker storage-worker frontend
    Pop-Location
}
Write-Host "Backup saved to $backupPath. It contains biometric data and encryption keys."
