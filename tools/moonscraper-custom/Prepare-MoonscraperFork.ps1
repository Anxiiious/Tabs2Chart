param(
    [string]$Destination = (Join-Path $PSScriptRoot '..\..\.build\moonscraper-custom')
)

$ErrorActionPreference = 'Stop'
$upstream = 'https://github.com/FireFox2000000/Moonscraper-Chart-Editor.git'
$revision = 'cb4c7a8c95f9e09f73ea6c2878b3a7ce5e0baeb0'
$patch = Join-Path $PSScriptRoot 'Tabs2Chart.patch'
$destinationPath = [System.IO.Path]::GetFullPath($Destination)

if (Test-Path -LiteralPath $destinationPath) {
    throw "Destination already exists; preserving it unchanged: $destinationPath"
}

git clone $upstream $destinationPath
if ($LASTEXITCODE -ne 0) {
    throw 'Could not clone the MoonScraper source.'
}

git -C $destinationPath checkout --detach $revision
if ($LASTEXITCODE -ne 0) {
    throw "Could not check out pinned revision $revision."
}

git -C $destinationPath apply --check $patch
if ($LASTEXITCODE -ne 0) {
    throw 'Tabs2Chart.patch does not apply cleanly to the pinned source.'
}
git -C $destinationPath apply $patch
if ($LASTEXITCODE -ne 0) {
    throw 'Could not apply Tabs2Chart.patch.'
}

Write-Output "Prepared custom MoonScraper source at $destinationPath"
