param(
    [string]$Source = (Join-Path $PSScriptRoot '..\..\.build\moonscraper-custom'),
    [string]$Output = (Join-Path $PSScriptRoot '..\..\dist\Moonscraper-Tabs2Chart'),
    [string]$UnityExe = 'C:\Program Files\Unity\Hub\Editor\2018.4.23f1\Editor\Unity.exe'
)

$ErrorActionPreference = 'Stop'
$sourcePath = [System.IO.Path]::GetFullPath($Source)
$outputPath = [System.IO.Path]::GetFullPath($Output)
$projectPath = Join-Path $sourcePath 'Moonscraper Chart Editor'

if (-not (Test-Path -LiteralPath $UnityExe -PathType Leaf)) {
    throw "Unity 2018.4.23f1 was not found at $UnityExe. Install that exact editor or pass -UnityExe."
}
if (-not (Test-Path -LiteralPath $projectPath -PathType Container)) {
    throw "Prepared source was not found at $projectPath. Run Prepare-MoonscraperFork.ps1 first."
}
if (-not (Test-Path -LiteralPath $outputPath)) {
    New-Item -ItemType Directory -Path $outputPath | Out-Null
}

& $UnityExe `
    -batchmode `
    -quit `
    -projectPath $projectPath `
    -executeMethod BuildManager.BuildTabs2ChartWindows64 `
    -moonscraperBuildPath $outputPath `
    -logFile (Join-Path $outputPath 'unity-build.log')

if ($LASTEXITCODE -ne 0) {
    throw "Unity build failed with exit code $LASTEXITCODE. See unity-build.log."
}

Write-Output "Built custom MoonScraper under $outputPath"
