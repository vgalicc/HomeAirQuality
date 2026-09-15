# Wrapper for Task Scheduler: scrape, then commit CSV readings to GitHub.
# Scraper also writes its own log to data\scrape.log.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$logDir = Join-Path $root 'data'
$log = Join-Path $logDir 'scrape.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-WrapperLog([string]$Message) {
    Add-Content -Path $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ERROR   wrapper: $Message" -Encoding utf8
}

function Find-Python {
    $candidates = @(
        'C:\Python311\python.exe',
        'C:\Python312\python.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) {
            return $path
        }
    }
    $cmd = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) {
        return $cmd.Source
    }
    throw 'Python not found. Install Python or update the path in run_scrape.ps1.'
}

function Find-Git {
    $candidates = @(
        'C:\Program Files\Git\cmd\git.exe',
        'C:\Program Files\Git\bin\git.exe'
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) {
            return $path
        }
    }
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    throw 'Git not found. Install Git for Windows.'
}

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$GitArgs)
    & $script:GitExe -C $root @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit $LASTEXITCODE"
    }
}

function Sync-FromGitHub {
    $env:GIT_TERMINAL_PROMPT = '0'
    Invoke-Git -GitArgs @('pull', '--rebase', '--autostash', 'origin', 'main')
}

function Push-ReadingsToGitHub {
    $env:GIT_TERMINAL_PROMPT = '0'
    Invoke-Git -GitArgs @('add', '--', 'data')
    & $script:GitExe -C $root diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        return
    }
    if ($LASTEXITCODE -ne 1) {
        throw "git diff --cached --quiet failed with exit $LASTEXITCODE"
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyy-MM-dd HH:mm')
    $msg = "O$([char]0x010D)itanje $stamp UTC"
    Invoke-Git -GitArgs @('commit', '-m', $msg)
    Invoke-Git -GitArgs @('pull', '--rebase', '--autostash', 'origin', 'main')
    Invoke-Git -GitArgs @('push', 'origin', 'main')
}

try {
    $python = Find-Python
    $script:GitExe = Find-Git
    $env:PYTHONIOENCODING = 'utf-8'

    Sync-FromGitHub

    & $python (Join-Path $root 'scrape.py')
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Push-ReadingsToGitHub
    exit 0
} catch {
    Write-WrapperLog "$_"
    throw
}
