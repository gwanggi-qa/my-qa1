$ErrorActionPreference = 'Stop'
$command = Get-Command appium.cmd -ErrorAction Stop
if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk" }
if (-not $env:APPIUM_HOME) { $env:APPIUM_HOME = "$env:USERPROFILE\.appium" }
& $command.Source --address 127.0.0.1 --port 4725
exit $LASTEXITCODE
