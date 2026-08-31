param(
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$env:Path = "C:\Users\mcspd\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin;$env:Path"

$sourceStatus = @(git -C $PSScriptRoot status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the source tree before deployment."
}
if ($sourceStatus.Count -ne 0) {
    throw "Refusing to deploy a dirty source tree."
}

$sourceRevision = (git -C $PSScriptRoot rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceRevision -notmatch "^[0-9a-f]{40}$") {
    throw "Could not resolve one full Git source revision before deployment."
}

$deployArguments = @(
    "run"
    "deploy"
    "specguard"
    "--source"
    $PSScriptRoot
    "--region"
    "us-central1"
    "--service-account"
    "specguard-runtime@specguard-hack.iam.gserviceaccount.com"
    "--allow-unauthenticated"
    "--min-instances"
    "0"
    "--max-instances"
    "1"
    "--concurrency"
    "2"
    "--set-env-vars"
    "SPECGUARD_PROJECT=specguard-hack,SPECGUARD_RUNS_BUCKET=specguard-hack-runs,SPECGUARD_GEMMA_ENDPOINT=disabled,SPECGUARD_TRUST_FORWARDED_FOR=1,SPECGUARD_AGENT_MODE=full_text,SPECGUARD_SOURCE_REVISION=$sourceRevision"
    "--update-labels"
    "specguard-source-revision=$sourceRevision"
    "--set-secrets"
    "SPECGUARD_DEMO_PASSPHRASE=specguard-demo-passphrase:latest,SPECGUARD_GEMMA_KEY=specguard-gemma-key:latest"
    "--project"
    "specguard-hack"
)

if ($PlanOnly) {
    Write-Output "Source revision: $sourceRevision"
    Write-Output "Cloud Run label: specguard-source-revision=$sourceRevision"
    Write-Output "No Cloud resource was changed."
    exit 0
}

gcloud @deployArguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
