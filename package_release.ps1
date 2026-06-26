$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = (Resolve-Path -LiteralPath $scriptRoot).Path
$releaseDir = Join-Path $workspaceRoot 'release'
$sourceExe = Join-Path $workspaceRoot 'target\release\board_cut_optimizer.exe'
$stageExe = Join-Path $releaseDir 'board_cut_optimizer.exe'
$version = 'V1.2.1'
$finalExeName = ('{0}{1}{2} {3}.exe' -f ([char]20248), ([char]26495), ([char]25490), $version)
$finalExe = Join-Path $releaseDir $finalExeName

if (-not (Test-Path -LiteralPath $sourceExe)) {
    throw "Missing build artifact: $sourceExe"
}

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

Get-ChildItem -LiteralPath $releaseDir -Force | ForEach-Object {
    $resolved = (Resolve-Path -LiteralPath $_.FullName).Path
    if ($resolved -notlike "$workspaceRoot*") {
        throw "Refusing to delete outside workspace: $resolved"
    }
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
}

[System.IO.File]::Copy($sourceExe, $stageExe, $true)
Move-Item -LiteralPath $stageExe -Destination $finalExe -Force

Write-Host "Created: $finalExe"
