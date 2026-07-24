param(
    [string]$InstalledMoonScraper = 'C:\Program Files (x86)\Moonscraper Chart Editor',
    [string]$Output = (Join-Path $PSScriptRoot '..\..\dist\Moonscraper-Tabs2Chart')
)

$ErrorActionPreference = 'Stop'
$outputPath = [System.IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) {
    throw "Output already exists; preserving it unchanged: $outputPath"
}
if (-not (Test-Path -LiteralPath $InstalledMoonScraper -PathType Container)) {
    throw "Installed MoonScraper folder was not found: $InstalledMoonScraper"
}

$release = Invoke-RestMethod `
    -Uri 'https://api.github.com/repos/BepInEx/BepInEx/releases/latest' `
    -Headers @{'User-Agent'='Tabs2Chart'}
$asset = $release.assets |
    Where-Object { $_.name -match '^BepInEx_win_x64_.*\.zip$' } |
    Select-Object -First 1
if (-not $asset) {
    throw 'The current BepInEx release has no Windows x64 archive.'
}

$download = Join-Path ([System.IO.Path]::GetTempPath()) $asset.name
$extract = Join-Path ([System.IO.Path]::GetTempPath()) (
    'tabs2chart-bepinex-' + [guid]::NewGuid().ToString('N')
)
Copy-Item -LiteralPath $InstalledMoonScraper -Destination $outputPath -Recurse
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $download
Expand-Archive -LiteralPath $download -DestinationPath $extract
Copy-Item -Path (Join-Path $extract '*') -Destination $outputPath -Recurse

$plugins = Join-Path $outputPath 'BepInEx\plugins'
New-Item -ItemType Directory -Path $plugins -Force | Out-Null
$managed = Join-Path $outputPath 'Moonscraper Chart Editor_Data\Managed'
$core = Join-Path $outputPath 'BepInEx\core'
$sdk = dotnet --list-sdks |
    ForEach-Object { ($_ -split ' ')[0] } |
    Sort-Object { [version]$_ } -Descending |
    Select-Object -First 1
if (-not $sdk) {
    throw '.NET SDK is required to compile the alignment plugin.'
}
$csc = "C:\Program Files\dotnet\sdk\$sdk\Roslyn\bincore\csc.dll"
$pluginSource = Join-Path $PSScriptRoot 'Tabs2ChartAlignmentPlugin.cs'
$pluginOutput = Join-Path $plugins 'Tabs2ChartAlignmentPlugin.dll'

dotnet $csc /nologo /target:library /nostdlib /out:$pluginOutput `
    /reference:"$managed\mscorlib.dll" `
    /reference:"$managed\netstandard.dll" `
    /reference:"$managed\Assembly-CSharp.dll" `
    /reference:"$managed\UnityEngine.dll" `
    /reference:"$managed\UnityEngine.CoreModule.dll" `
    /reference:"$managed\UnityEngine.InputModule.dll" `
    /reference:"$managed\UnityEngine.IMGUIModule.dll" `
    /reference:"$managed\UnityEngine.UI.dll" `
    /reference:"$core\BepInEx.dll" `
    /reference:"$core\0Harmony.dll" `
    $pluginSource
if ($LASTEXITCODE -ne 0) {
    throw "Alignment plugin compilation failed with exit code $LASTEXITCODE."
}

Write-Output "Built runnable custom MoonScraper copy at $outputPath"
