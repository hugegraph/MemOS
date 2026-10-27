# Launch the existing frontend against the real backend; never edit .env.local.
$ErrorActionPreference = 'Stop'
$taskRoot = 'D:/MemOS'
$dashboard = Join-Path $taskRoot 'dashboard'
$statePath = Join-Path $taskRoot '.local/processes.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json

function Stop-OwnedListener([int]$Port, [string]$Name, [string]$Marker) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $service = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
        $entry = $state.PSObject.Properties[$Name]
        if (-not $entry -or $entry.Value.id -ne $service.ProcessId -or
            $service.ExecutablePath -ne $entry.Value.executable -or
            -not $service.CommandLine.Replace('/', '\').Contains($Marker)) {
            throw "Port $Port is not the recorded owned $Name process; refusing to stop it"
        }
        Stop-Process -Id $service.ProcessId -ErrorAction Stop
    }
}

$health = Invoke-RestMethod -Uri 'http://127.0.0.1:5281/health'
if ($health.code -ne 0) { throw 'Real backend is not healthy' }
Stop-OwnedListener 5280 'mock' 'server\mock\server.ts'
Stop-OwnedListener 5274 'web' 'vite\bin\vite.js'
$env:MOCK_ORIGIN = 'http://127.0.0.1:5281'
$node = 'E:/nodejs/node.exe'
$vite = Join-Path $dashboard 'node_modules/vite/bin/vite.js'
$service = Start-Process -FilePath $node -ArgumentList @($vite, '--host', '127.0.0.1', '--port', '5274', '--strictPort') -WorkingDirectory $dashboard -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot '.local/logs/web-live.out.log') -RedirectStandardError (Join-Path $taskRoot '.local/logs/web-live.err.log') -PassThru
$entry = [pscustomobject]@{id=$service.Id; executable=$node.Replace('/', '\'); marker='vite\bin\vite.js'; port=5274; mode='live'}
$state | Add-Member -MemberType NoteProperty -Name web -Value $entry -Force
$state.PSObject.Properties.Remove('mock')
$state | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $statePath -Encoding UTF8
Write-Host 'Mock stopped. Main frontend 5274 now proxies real backend 5281.'
