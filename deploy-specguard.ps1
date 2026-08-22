$ErrorActionPreference = "Stop"
$env:Path = "C:\Users\mcspd\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin;$env:Path"

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
    "SPECGUARD_PROJECT=specguard-hack,SPECGUARD_RUNS_BUCKET=specguard-hack-runs,SPECGUARD_GEMMA_ENDPOINT=disabled"
    "--set-secrets"
    "SPECGUARD_DEMO_PASSPHRASE=specguard-demo-passphrase:latest,SPECGUARD_GEMMA_KEY=specguard-gemma-key:latest"
    "--project"
    "specguard-hack"
)

gcloud @deployArguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
