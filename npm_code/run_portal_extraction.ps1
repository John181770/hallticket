$ErrorActionPreference = "Stop"

Set-Location -LiteralPath "C:\Users\pope1\Documents\Codex\2026-05-18\task-extract-mbbs-exam-results-from"

$transcriptPath = Join-Path (Get-Location) "portal_extraction_terminal.log"
Start-Transcript -LiteralPath $transcriptPath -Append | Out-Null

Write-Host ""
Write-Host "NTRUHS result extraction runner"
Write-Host "Input:  hall_tickets.csv"
Write-Host "Output: exam_results_live.csv"
Write-Host "Log:    exam_results_errors_live.csv"
Write-Host ""
Write-Host "A browser window will open. For each hall ticket, type the CAPTCHA shown in the browser into this terminal."
Write-Host "You can stop with Ctrl+C and run this file again later; --resume will skip completed hall tickets."
Write-Host "Terminal transcript: $transcriptPath"
Write-Host ""

& node ".\extract_mbbs_results.mjs" `
  --input ".\hall_tickets.csv" `
  --url "http://results.uhsap.in/main_result?result=kKJFRFKdGMyT%2b6%2fe4faAQQ%3d%3d" `
  --headful `
  --browser-channel "msedge" `
  --output ".\exam_results_live.csv" `
  --error-output ".\exam_results_errors_live.csv" `
  --resume

Stop-Transcript | Out-Null
