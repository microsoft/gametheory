#requires -Version 7.2
<#
.SYNOPSIS
Builds the validation (test) image from this checkout in a registry and prints it by digest.

.DESCRIPTION
Uses ACR Tasks, so Docker is not needed locally. Pass the printed reference as the studio
template's validationImage parameter together with deployProbeJobs=true.
See docs/live-acceptance.md.

.EXAMPLE
pwsh ./scripts/acceptance/Publish-ValidationImage.ps1 -Registry <registry-name>
#>
[CmdletBinding()]
param([Parameter(Mandatory)] [string] $Registry)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AcceptanceCommon.psm1') -Force

Publish-SourceImage -Registry $Registry -Repository 'gametheory-validation' -Target 'validation'
