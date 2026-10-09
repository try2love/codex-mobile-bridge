param(
    [switch]$Brokered,
    [ValidatePattern('^[a-f0-9]{32}$')][string]$Ticket
)
$ErrorActionPreference = 'Stop'
$previewRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appPath = Join-Path $previewRoot 'dist\desktop\win-unpacked\Codex Mobile Bridge.exe'
$dataDirectory = Join-Path $previewRoot '.local\windows-preview3-user'
$launchDirectory = Join-Path $previewRoot '.local\windows-preview3-launch'
$utf8 = New-Object Text.UTF8Encoding($false)

function Read-ProcessRecord([int]$ProcessId) {
    $record = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $ProcessId)
    if ($null -eq $record) { throw 'The preview launch process disappeared.' }
    return $record
}
function Confirm-Image($Record, [string]$ExpectedPath) {
    if ([string]::IsNullOrEmpty($Record.ExecutablePath) -or
        -not [string]::Equals([IO.Path]::GetFullPath($Record.ExecutablePath), $ExpectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The preview launch process identity changed.'
    }
}
function Save-Result([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 6 -Compress), $utf8)
}
function Confirm-ExplorerParent {
    $current = Read-ProcessRecord $PID
    $command = Read-ProcessRecord ([int]$current.ParentProcessId)
    $explorer = Read-ProcessRecord ([int]$command.ParentProcessId)
    Confirm-Image $command (Join-Path $env:SystemRoot 'System32\cmd.exe')
    Confirm-Image $explorer (Join-Path $env:SystemRoot 'explorer.exe')
    if ($command.SessionId -ne $current.SessionId -or $explorer.SessionId -ne $current.SessionId -or
        $command.CreationDate -gt $current.CreationDate -or $explorer.CreationDate -gt $command.CreationDate) {
        throw 'The Explorer launch parent could not be verified.'
    }
    return @{
        brokerPid = $PID
        commandPid = [int]$command.ProcessId
        explorerPid = [int]$explorer.ProcessId
        explorerCreated = $explorer.CreationDate.ToUniversalTime().Ticks.ToString()
        sessionId = [int]$current.SessionId
    }
}

if ($Brokered) {
    if (-not $Ticket) { throw 'The internal launch ticket is missing.' }
    $replyPath = Join-Path $launchDirectory ($Ticket + '.json')
    try {
        $parents = Confirm-ExplorerParent
        if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
            throw 'Build the Windows app first. See docs\windows-preview3-local.md.'
        }
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $appPath
        $start.WorkingDirectory = $previewRoot
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
        foreach ($key in @($start.EnvironmentVariables.Keys)) {
            if ($key -eq 'ELECTRON_RUN_AS_NODE' -or $key -like 'CMB_UPDATE_*') {
                $start.EnvironmentVariables.Remove($key)
            }
        }
        $start.EnvironmentVariables['CMB_DATA_DIR'] = $dataDirectory
        $app = [Diagnostics.Process]::Start($start)
        if ($null -eq $app) { throw 'The preview app did not start.' }
        Start-Sleep -Milliseconds 1200
        $app.Refresh()
        if ($app.HasExited) {
            throw 'The new preview process exited. Close any existing preview controller, then run this launcher again.'
        }
        $record = Read-ProcessRecord $app.Id
        Confirm-Image $record $appPath
        if ($record.ParentProcessId -ne $PID -or $record.SessionId -ne $parents.sessionId) {
            throw 'The preview app parent could not be verified.'
        }
        $reply = @{
            ok = $true; ticket = $Ticket; pid = $app.Id
            created = $app.StartTime.ToUniversalTime().Ticks.ToString()
            executable = $appPath; dataDirectory = $dataDirectory; parents = $parents
        }
        Save-Result $replyPath $reply
    } catch {
        Save-Result $replyPath @{ok = $false; ticket = $Ticket; error = $_.Exception.Message}
        exit 1
    }
    exit 0
}

try {
    if ($Ticket) { throw 'Launch tickets are internal to the Explorer broker.' }
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
        throw 'Build the Windows app first. See docs\windows-preview3-local.md.'
    }
    New-Item -ItemType Directory -Force -Path $launchDirectory | Out-Null
    $launchTicket = [Guid]::NewGuid().ToString('N')
    $wrapperPath = Join-Path $launchDirectory ($launchTicket + '.cmd')
    $replyPath = Join-Path $launchDirectory ($launchTicket + '.json')
    # The only interpolated batch argument is a generated hexadecimal ticket.
    # The fixed relative path also handles spaces, Unicode, percent and ! in the checkout.
    $wrapper = "@echo off" + [Environment]::NewLine +
        "setlocal DisableDelayedExpansion" + [Environment]::NewLine +
        '"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0..\..\scripts\start-preview3-windows.ps1" -Brokered -Ticket ' +
        $launchTicket + [Environment]::NewLine
    [IO.File]::WriteAllText($wrapperPath, $wrapper, [Text.Encoding]::ASCII)
    $shell = New-Object -ComObject Shell.Application
    $windows = $shell.Windows()
    $desktopHandle = 0
    $desktop = $windows.FindWindowSW(0, 0, 8, [ref]$desktopHandle, 1)
    if ($null -eq $desktop) { throw 'Explorer desktop broker is unavailable.' }
    # Invoke the existing Explorer desktop, rather than starting a child of the terminal.
    $desktop.Document.Application.ShellExecute($wrapperPath, '', $launchDirectory, 'open', 0)
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    $reply = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $replyPath -PathType Leaf) {
            try { $reply = Get-Content -LiteralPath $replyPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $reply = $null }
            if ($null -ne $reply) { break }
        }
        Start-Sleep -Milliseconds 100
    }
    if ($null -eq $reply) { throw 'Explorer did not acknowledge preview startup. No terminal-child fallback was used.' }
    if ($reply.ticket -ne $launchTicket -or $reply.ok -ne $true) {
        throw $(if ($reply.error) { [string]$reply.error } else { 'Invalid preview startup receipt.' })
    }
    if ($reply.executable -ne $appPath -or $reply.dataDirectory -ne $dataDirectory) { throw 'Preview startup configuration changed.' }
    $running = Get-Process -Id ([int]$reply.pid) -ErrorAction Stop
    if ($running.HasExited -or $running.StartTime.ToUniversalTime().Ticks.ToString() -ne $reply.created) {
        throw 'The preview app exited or changed before startup was confirmed.'
    }
    Confirm-Image (Read-ProcessRecord $running.Id) $appPath
    $explorer = Read-ProcessRecord ([int]$reply.parents.explorerPid)
    Confirm-Image $explorer (Join-Path $env:SystemRoot 'explorer.exe')
    if ($explorer.CreationDate.ToUniversalTime().Ticks.ToString() -ne $reply.parents.explorerCreated) {
        throw 'The Explorer launch parent changed during startup.'
    }
    Save-Result (Join-Path $launchDirectory 'latest.json') $reply
    Write-Host ('Preview started independently through Explorer. PID: ' + $reply.pid)
    Write-Host ('Startup receipt: ' + (Join-Path $launchDirectory 'latest.json'))
} catch {
    Write-Error ('Preview startup failed: ' + $_.Exception.Message + ' No direct-launch fallback was used.')
    exit 1
}
