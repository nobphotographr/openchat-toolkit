param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!$PythonPath) {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $PythonPath = & py.exe -3 -c 'import sys; print(sys.executable)'
    } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        $PythonPath = (Get-Command python.exe).Source
    } else { throw 'Python 3.11 or newer is required. Install Python, then run setup again.' }
}
& $PythonPath -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python version check failed' }
$runtime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $runtime)) {
    & $PythonPath -m venv --without-pip (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Creating private runtime failed' }
}
& $runtime (Join-Path $PSScriptRoot 'build.py')
if ($LASTEXITCODE -ne 0) { throw 'Native build failed' }
& $runtime -c 'from settings import ensure_home; ensure_home()'
if ($LASTEXITCODE -ne 0) { throw 'Creating empty settings failed' }
Write-Host 'Setup completed. Open Start.cmd. No scheduled task was registered.'
