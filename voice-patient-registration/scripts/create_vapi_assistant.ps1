# ---------------------------------------------------------------------------
# Creates the Vapi assistant, with its system prompt and all tools, in a single
# API call. Doing this from a versioned file rather than by clicking through the
# dashboard means the assistant configuration is reviewable in the repository
# and reproducible if it ever needs to be rebuilt.
#
# Usage (PowerShell, from the repo root):
#   .\scripts\create_vapi_assistant.ps1 -VapiPrivateKey "..." -BaseUrl "https://your-app.onrender.com" -ServerSecret "..."
# ---------------------------------------------------------------------------

param(
  [Parameter(Mandatory = $true)][string]$VapiPrivateKey,
  [Parameter(Mandatory = $true)][string]$BaseUrl,
  [Parameter(Mandatory = $true)][string]$ServerSecret
)

# Trim a trailing slash so we never build a "//vapi/tools" URL.
$BaseUrl = $BaseUrl.TrimEnd('/')

$configPath = Join-Path $PSScriptRoot "..\prompts\vapi_assistant.json"
if (-not (Test-Path $configPath)) { throw "Cannot find prompts/vapi_assistant.json" }

# Substitute placeholders. The secret never touches the repository.
$json = (Get-Content $configPath -Raw).
          Replace("BASE_URL", $BaseUrl).
          Replace("YOUR_VAPI_SERVER_SECRET", $ServerSecret)

# Sanity-check the JSON locally before spending a round trip on it.
try { $null = $json | ConvertFrom-Json }
catch { Write-Host "prompts/vapi_assistant.json is not valid JSON." -ForegroundColor Red; exit 1 }

Write-Host "Creating assistant -> $BaseUrl/vapi/tools" -ForegroundColor Cyan

# Invoke-WebRequest rather than Invoke-RestMethod: on a 4xx, PowerShell 5.1
# discards the response body from Invoke-RestMethod, and the body is the only
# place Vapi says WHICH field it rejected.
try {
  $resp = Invoke-WebRequest `
    -Uri "https://api.vapi.ai/assistant" `
    -Method Post `
    -Headers @{ Authorization = "Bearer $VapiPrivateKey" } `
    -ContentType "application/json" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($json)) `
    -UseBasicParsing

  $created = $resp.Content | ConvertFrom-Json
  Write-Host ""
  Write-Host "Assistant created." -ForegroundColor Green
  Write-Host ("  Name: " + $created.name)
  Write-Host ("  ID:   " + $created.id)
  Write-Host ""
  Write-Host "Next: Vapi dashboard -> Phone Numbers -> buy a US number ->" -ForegroundColor Yellow
  Write-Host "      set its inbound assistant to this one, then call it."   -ForegroundColor Yellow
}
catch {
  Write-Host ""
  Write-Host "Failed to create assistant." -ForegroundColor Red

  $body = $null

  # PowerShell 7+ exposes the body directly.
  if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
    $body = $_.ErrorDetails.Message
  }
  # PowerShell 5.1: read it off the raw response stream.
  elseif ($_.Exception.Response) {
    try {
      $stream = $_.Exception.Response.GetResponseStream()
      $stream.Position = 0
      $body = (New-Object System.IO.StreamReader($stream)).ReadToEnd()
    } catch { }
  }

  if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
    Write-Host ("  HTTP " + [int]$_.Exception.Response.StatusCode + " " + $_.Exception.Response.StatusCode) -ForegroundColor Yellow
  }
  if ($body) {
    Write-Host "  Response body:" -ForegroundColor Yellow
    Write-Host $body
  } else {
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Yellow
  }
  exit 1
}
