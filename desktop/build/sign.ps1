param(
  [Parameter(Mandatory=$true)][string]$Exe,
  [string]$TimestampUrl = "http://timestamp.digicert.com"
)
# Release signing hook. Requires a code-signing certificate in the release
# environment; CI without a certificate must not pretend the build is signed.
$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
  Write-Output "SIGNTOOL_NOT_FOUND"
  exit 2
}
$pfx = $env:MEETINGVAR_SIGN_CERT
$password = $env:MEETINGVAR_SIGN_PASSWORD
$thumbprint = $env:MEETINGVAR_SIGN_THUMBPRINT
if ($thumbprint) {
  & $signtool.Source sign /sha1 $thumbprint /fd sha256 /tr $TimestampUrl /td sha256 $Exe
} elseif ($pfx -and (Test-Path $pfx)) {
  & $signtool.Source sign /f $pfx /p $password /fd sha256 /tr $TimestampUrl /td sha256 $Exe
} else {
  Write-Output "SIGNING_CERT_NOT_CONFIGURED"
  exit 3
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $signtool.Source verify /pa /v $Exe
exit $LASTEXITCODE