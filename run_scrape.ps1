# Omotač za Task Scheduler: postavi radni direktorij i pokreni scraper.
# Izlaz se dodatno zapisuje u data\scrape.log (sam scraper vodi svoj log).

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = 'C:\Python311\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

$env:PYTHONIOENCODING = 'utf-8'
& $python (Join-Path $root 'scrape.py')
exit $LASTEXITCODE
