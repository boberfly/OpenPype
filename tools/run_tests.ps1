<#
.SYNOPSIS
  Helper script to run tests for OpenPype.

.DESCRIPTION
  This will use virtual environment and pytest to run test for OpenPype.

.EXAMPLE

PS> .\run_test.ps1

#>

$current_dir = Get-Location
$script_dir = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$openpype_root = (Get-Item $script_dir).parent.FullName

# Install PSWriteColor to support colorized output to terminal
$env:PSModulePath = $env:PSModulePath + ";$($openpype_root)\tools\modules\powershell"

function Exit-WithCode($exitcode) {
   # Only exit this host process if it's a child of another PowerShell parent process...
   $parentPID = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$PID" | Select-Object -Property ParentProcessId).ParentProcessId
   $parentProcName = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$parentPID" | Select-Object -Property Name).Name
   if ('powershell.exe' -eq $parentProcName) { $host.SetShouldExit($exitcode) }

   exit $exitcode
}

function Show-PSWarning() {
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        Write-Color -Text "!!! ", "You are using old version of PowerShell - ",  "$($PSVersionTable.PSVersion.Major).$($PSVersionTable.PSVersion.Minor)" -Color Red, Yellow, White
        Write-Color -Text "    Please update to at least 7.0 - ", "https://github.com/PowerShell/PowerShell/releases" -Color Yellow, White
        Exit-WithCode 1
    }
}

$art = @"

             . .   ..     .    ..
        _oOOP3OPP3Op_. .
     .PPpo~.   ..   ~2p.  ..  ....  .  .
    .Ppo . .pPO3Op.. . O:. . . .
   .3Pp . oP3'. 'P33. . 4 ..   .  .   . .. .  .  .
  .~OP    3PO.  .Op3    : . ..  _____  _____  _____
  .P3O  . oP3oP3O3P' . . .   . /    /./    /./    /
   O3:.   O3p~ .       .:. . ./____/./____/ /____/
   'P .   3p3.  oP3~. ..P:. .  . ..  .   . .. .  .  .
  . ':  . Po'  .Opo'. .3O. .  o[ by Pype Club ]]]==- - - .  .
    . '_ ..  .    . _OP3..  .  .https://openpype.io.. .
         ~P3.OPPPO3OP~ . ..  .
           .  ' '. .  .. . . . ..  .

"@

Write-Host $art -ForegroundColor DarkGreen

# Enable if PS 7.x is needed.
# Show-PSWarning

$env:_INSIDE_OPENPYPE_TOOL = "1"

if (-not (Test-Path 'env:POETRY_HOME')) {
    $env:POETRY_HOME = "$openpype_root\.poetry"
}

Set-Location -Path $openpype_root

$version_file = Get-Content -Path "$($openpype_root)\openpype\version.py"
$result = [regex]::Matches($version_file, '__version__ = "(?<version>\d+\.\d+.\d+.*)"')
$openpype_version = $result[0].Groups['version'].Value
if (-not $openpype_version) {
  Write-Color -Text "!!! ", "Cannot determine OpenPype version." -Color Yellow, Gray
  Exit-WithCode 1
}

Write-Color -Text ">>> ", "OpenPype [ ", $openpype_version, " ]" -Color Green, White, Cyan, White

Write-Color -Text ">>> ", "Reading Poetry ... " -Color Green, Gray -NoNewline
if (-not (Test-Path -PathType Container -Path "$($env:POETRY_HOME)\bin")) {
    Write-Color -Text "NOT FOUND" -Color Yellow
    Write-Color -Text "*** ", "We need to install Poetry create virtual env first ..." -Color Yellow, Gray
    & "$openpype_root\tools\create_env.ps1"
} else {
    Write-Color -Text "OK" -Color Green
}

Write-Color -Text ">>> ", "Cleaning cache files ... " -Color Green, Gray -NoNewline
Get-ChildItem $openpype_root -Filter "*.pyc" -Force -Recurse | Where-Object { $_.FullName -inotmatch 'build' } | Remove-Item -Force
Get-ChildItem $openpype_root -Filter "*.pyo" -Force -Recurse | Where-Object { $_.FullName -inotmatch 'build' } | Remove-Item -Force
Get-ChildItem $openpype_root -Filter "__pycache__" -Force -Recurse | Where-Object { $_.FullName -inotmatch 'build' } | Remove-Item -Force -Recurse
Write-Color -Text "OK" -Color green

Write-Color -Text ">>> ", "Testing OpenPype ..." -Color Green, White
$original_pythonpath = $env:PYTHONPATH
$env:PYTHONPATH="$($openpype_root);$($env:PYTHONPATH)"
& "$env:POETRY_HOME\bin\poetry" run pytest -x --capture=sys --print -W ignore::DeprecationWarning "$($openpype_root)/tests"
$env:PYTHONPATH = $original_pythonpath

Write-Color -Text ">>> ", "Restoring current directory" -Color Green, Gray
Set-Location -Path $current_dir
