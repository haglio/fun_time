$gitBash = "C:\Program Files\Git\bin\bash.exe"
$staleEntry = "C:\Users\Example\miniconda3\bin"

$pathEntries = $env:PATH -split ";" | Where-Object { $_ -and $_ -ne $staleEntry }
$env:PATH = ($pathEntries | Select-Object -Unique) -join ";"

# Keep Conda available while avoiding an automatic "(base)" prompt in new shells.
$env:CONDA_AUTO_ACTIVATE_BASE = "false"

& $gitBash --login -i
exit $LASTEXITCODE
