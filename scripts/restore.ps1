param([Parameter(Mandatory=$true)][string]$Source,[Parameter(Mandatory=$true)][ValidatePattern('^[a-z][a-z0-9-]+$')][string]$Project)
$ErrorActionPreference = 'Stop'
if ($Project -eq 'prohod') { throw 'Restore only to a new project; the working project prohod is protected.' }
$workspacePath = Split-Path -Parent $PSScriptRoot
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
foreach ($file in @('manifest.json','prohod.dump','keycloak.dump','objects.tar.gz','bootstrap_secrets.tar.gz','runtime_secrets.tar.gz','certificates.tar.gz')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourcePath $file))) { throw "Missing backup file: $file" }
}
$existing = & docker volume ls --format '{{.Name}}'
if ($existing | Where-Object { $_.StartsWith($Project + '_') }) { throw 'Target project already has volumes; choose a new project name.' }
function DockerStep([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker restore step failed.' }
}
Push-Location $workspacePath
try {
    foreach ($volumeKind in @('objects','bootstrap_secrets','runtime_secrets','certificates')) {
        DockerStep @('volume','create',"${Project}_${volumeKind}")
        DockerStep @('run','--rm','--mount',"type=volume,source=${Project}_${volumeKind},target=/target",'--mount',"type=bind,source=$sourcePath,target=/backup,readonly",'alpine:3.22.0','tar','xzf',"/backup/$volumeKind.tar.gz",'-C','/target')
    }
    DockerStep @('compose','-p',$Project,'up','-d','postgres')
    for ($attempt=0;$attempt -lt 60;$attempt++) {
        & docker compose -p $Project exec -T postgres pg_isready -U prohod_owner -d prohod | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 2
    }
    DockerStep @('compose','-p',$Project,'cp',(Join-Path $sourcePath 'prohod.dump'),'postgres:/tmp/prohod.dump')
    DockerStep @('compose','-p',$Project,'cp',(Join-Path $sourcePath 'keycloak.dump'),'postgres:/tmp/keycloak.dump')
    DockerStep @('compose','-p',$Project,'exec','-T','postgres','sh','-c','PGPASSWORD=$(cat /bootstrap/db_owner) pg_restore --exit-on-error -U prohod_owner -d prohod /tmp/prohod.dump')
    DockerStep @('compose','-p',$Project,'exec','-T','postgres','sh','-c','PGPASSWORD=$(cat /bootstrap/db_owner) pg_restore --exit-on-error -U prohod_owner -d keycloak /tmp/keycloak.dump')
    Write-Host 'Restored to separate volumes. Stop the old proxy before starting the restored project on the same ports.'
    Write-Host "Start with: docker compose -p $Project up --build -d"
} finally { Pop-Location }
