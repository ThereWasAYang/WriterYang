Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Installer = Join-Path $ScriptDir "scripts\install_writeryang.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 $Installer @args
  exit $LASTEXITCODE
}

foreach ($Candidate in @("python", "python3")) {
  if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
    & $Candidate $Installer @args
    exit $LASTEXITCODE
  }
}

Write-Error "python is required to run the WriterYang installer."
exit 1
