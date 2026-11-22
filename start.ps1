param([string]$Python = "python", [int]$Port = 8017, [switch]$Stop)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONNOUSERSITE = "1"
if ($Stop) { & $Python -B -X utf8 run.py --stop }
else { & $Python -B -X utf8 run.py --port $Port }
exit $LASTEXITCODE
