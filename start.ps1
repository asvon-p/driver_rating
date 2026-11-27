param([string]$Python = "python", [int]$Port = 8017, [switch]$Stop, [switch]$Demo)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONNOUSERSITE = "1"
if ($Stop) { & $Python -B -X utf8 run.py --stop }
elseif ($Demo) { & $Python -B -X utf8 run.py --port $Port --demo }
else { & $Python -B -X utf8 run.py --port $Port }
exit $LASTEXITCODE
