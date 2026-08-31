param(
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
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

$snapshotRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("specguard-source-" + [guid]::NewGuid().ToString("N"))
$sourceArchive = Join-Path $snapshotRoot "source.zip"
$sourceSnapshot = Join-Path $snapshotRoot "source"

$deployArguments = @(
    "run"
    "deploy"
    "specguard"
    "--source"
    $sourceSnapshot
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

$deployExitCode = 0
try {
    New-Item -ItemType Directory -Path $snapshotRoot | Out-Null
    git -C $PSScriptRoot archive --format=zip --output=$sourceArchive $sourceRevision
    if ($LASTEXITCODE -ne 0) {
        throw "Could not archive the recorded Git source revision for deployment."
    }
    Expand-Archive -LiteralPath $sourceArchive -DestinationPath $sourceSnapshot

    gcloud @deployArguments
    $deployExitCode = $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $snapshotRoot -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $snapshotRoot) {
        Write-Warning "The temporary deployment source snapshot could not be removed: $snapshotRoot"
    }
}

if ($deployExitCode -ne 0) {
    exit $deployExitCode
}
