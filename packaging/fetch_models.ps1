<#
.SYNOPSIS
    Downloads the Docling models Label Miner uses into docling-models\ in the
    repository root, so the app (and the frozen .exe) never needs the network
    to load them.

.DESCRIPTION
    Only the two model sets the pipeline uses are fetched: "layout" (finds the
    text regions) and RapidOCR with the torch backend and its default "ch"
    models, which read Latin script - Danish included - well. The OCR engine
    is pinned in ocr.create_converter(); see the comment there.

    Run again after upgrading docling, in case it expects a newer model
    revision.
#>

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $root "docling-models"

Write-Host "Downloading the layout and RapidOCR (torch, ch) models into $outputDir ..."
docling-tools models download layout rapidocr -o "$outputDir" --rapidocr-backend-lang torch:ch
if ($LASTEXITCODE -ne 0) { throw "Downloading the models failed" }

Get-ChildItem -Recurse $outputDir | Measure-Object -Property Length -Sum |
    ForEach-Object { "Done: {0:N0} MB" -f ($_.Sum / 1MB) }
