# NEXORA — copy the server's daily backups down to this laptop.
#
# One-time setup (PowerShell):
#   1. ssh-keygen -t ed25519 -f $HOME\.ssh\nexora_backup -N '""'
#   2. Add $HOME\.ssh\nexora_backup.pub to /home/nexora/.ssh/authorized_keys on the server.
#   3. powershell -ExecutionPolicy Bypass -File deploy\laptop_backup.ps1 -Server <IP-or-domain> -Register
#      (registers a Windows scheduled task: daily 10:00, and catches up if the laptop was off)
#
# Manual run any time:
#   powershell -ExecutionPolicy Bypass -File deploy\laptop_backup.ps1 -Server <IP-or-domain>

param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$User = "nexora",
    [string]$KeyFile = "$HOME\.ssh\nexora_backup",
    [string]$Dest = "$HOME\Documents\NEXORA-Backups",
    [int]$KeepDays = 60,
    [switch]$Register
)

$ErrorActionPreference = "Stop"

# Retention: every daily backup is kept for $KeepDays days. In addition, the
# FIRST backup of each calendar month (per type: postgres / sqlite) is kept
# forever — a monthly snapshot even if the laptop/server was off on the 1st.
# The date comes from the file name (postgres_2026-12-01_2100.sql.gz), not the
# file's modified time, so copying/restoring files doesn't change what's kept.
function Invoke-NexoraRetention([string]$Dest, [int]$KeepDays) {
    $cutoff = (Get-Date).Date.AddDays(-$KeepDays)
    $files = foreach ($f in Get-ChildItem $Dest -Filter *.gz) {
        if ($f.Name -match '^(?<kind>[a-z]+)_(?<date>\d{4}-\d{2}-\d{2})_') {
            [pscustomobject]@{
                File  = $f
                Kind  = $Matches.kind
                Date  = [datetime]::ParseExact($Matches.date, 'yyyy-MM-dd', $null)
                Month = $Matches.date.Substring(0, 7)
            }
        }
    }
    $monthly = @{}
    foreach ($g in ($files | Group-Object Kind, Month)) {
        $first = $g.Group | Sort-Object { $_.File.Name } | Select-Object -First 1
        $monthly[$first.File.FullName] = $true
    }
    foreach ($x in $files) {
        if ($x.Date -lt $cutoff -and -not $monthly.ContainsKey($x.File.FullName)) {
            Remove-Item $x.File.FullName -Force
        }
    }
}

if ($Register) {
    $script = $MyInvocation.MyCommand.Path
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Server $Server -User $User -KeyFile `"$KeyFile`" -Dest `"$Dest`" -KeepDays $KeepDays"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
    $trigger = New-ScheduledTaskTrigger -Daily -At 10:00
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable
    Register-ScheduledTask -TaskName "NEXORA Laptop Backup" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Output "Scheduled task 'NEXORA Laptop Backup' registered (daily 10:00, runs late if laptop was off)."
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$log = Join-Path $Dest "backup-log.txt"

try {
    $sshOpts = @("-i", $KeyFile, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

    # Ask the server which backup files exist, copy only the ones we don't have yet.
    $remote = & ssh @sshOpts "$User@$Server" "ls -1 /var/backups/nexora/ 2>/dev/null"
    if ($LASTEXITCODE -ne 0) { throw "ssh failed (exit $LASTEXITCODE)" }

    $copied = 0
    foreach ($name in ($remote | Where-Object { $_ -match '\.(gz)$' })) {
        $local = Join-Path $Dest $name
        if (-not (Test-Path $local)) {
            & scp @sshOpts "${User}@${Server}:/var/backups/nexora/$name" "$local"
            if ($LASTEXITCODE -ne 0) { throw "scp failed for $name" }
            $copied++
        }
    }

    Invoke-NexoraRetention -Dest $Dest -KeepDays $KeepDays

    $latest = Get-ChildItem $Dest -Filter *.gz | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    "$(Get-Date -Format s)  OK  copied=$copied  latest=$($latest.Name)" | Add-Content $log
    Write-Output "Backup OK: $copied new file(s). Latest: $($latest.Name)"
}
catch {
    "$(Get-Date -Format s)  FAILED  $($_.Exception.Message)" | Add-Content $log
    Write-Error "NEXORA backup FAILED: $($_.Exception.Message)"
    exit 1
}
