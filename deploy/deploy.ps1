<#
.SYNOPSIS
  Generic deployment wrapper for the Azure Healthcare Digital Quality Platform.

.DESCRIPTION
  Drives the Terraform module under deploy/terraform for any supported target,
  then (optionally) builds and pushes container images for the chosen stack.

.EXAMPLE
  ./deploy.ps1 -Target azure -Stack submitters -Tag v1.0.0 -Action apply

.EXAMPLE
  ./deploy.ps1 -Target docker -Action apply -NoBuild
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('azure', 'aws', 'gcp', 'kubernetes', 'docker')]
    [string] $Target,

    [ValidateSet('consumers', 'providers', 'submitters', 'receivers', 'platform')]
    [string] $Stack = 'submitters',

    [string] $Tag = 'latest',

    [ValidateSet('plan', 'apply', 'destroy')]
    [string] $Action = 'apply',

    [switch] $NoBuild
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$tfDir     = Join-Path $scriptDir 'terraform'
$repoRoot  = Split-Path -Parent $scriptDir

Push-Location $tfDir
try {
    terraform init -upgrade -input=false

    $tfArgs = @(
        '-var', "target_platform=$Target",
        '-var', "stack=$Stack",
        '-var', "image_tag=$Tag"
    )

    switch ($Action) {
        'plan'    { terraform plan    -input=false @tfArgs; return }
        'destroy' { terraform destroy  -input=false -auto-approve @tfArgs; return }
    }

    # Phase 1: provision infra only.
    $infraTargets = @(
        '-target=module.azure',
        '-target=module.aws',
        '-target=module.gcp',
        '-target=module.kubernetes',
        '-target=module.docker'
    )
    terraform apply -input=false -auto-approve @tfArgs @infraTargets

    $registry = (& terraform output -raw registry_url 2>$null)

    # Phase 2: build + push images (docker target is handled inside Terraform).
    if (-not $NoBuild -and $Target -ne 'docker' -and $registry) {
        Write-Host "==> Building and pushing images to $registry"
        foreach ($svc in @('backend', 'frontend', 'orchestrator')) {
            $dockerfile = Join-Path $repoRoot "$Stack/$svc/Dockerfile"
            if (-not (Test-Path $dockerfile)) { Write-Host "skip $svc (no Dockerfile)"; continue }
            $image = "$registry/${svc}:$Tag"
            docker build -t $image -f $dockerfile $repoRoot
            docker push  $image
        }
    }

    # Phase 3: apply full graph (workload deployment).
    terraform apply -input=false -auto-approve @tfArgs

    Write-Host ''
    Write-Host '==> Done. Useful outputs:'
    terraform output
}
finally {
    Pop-Location
}
