$script = "scripts/download_matches.py"
$args = @("--team","9565")
$delay = 2
$maxDelay = 120
$attempt = 1

while ($true) {
  Write-Host "Attempt $attempt..."
  python $script @args
  if ($LASTEXITCODE -eq 0) { Write-Host "Success"; break }
  Write-Host "Failed. Sleeping $delay sec then retrying..."
  Start-Sleep -Seconds $delay
  $delay = [Math]::Min($delay, $maxDelay)
  $attempt++
}
