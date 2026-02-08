Get-ChildItem data/raw/hltv_match_veto_and_stats_*.jsonl | ForEach-Object {
    Write-Host "Ingesting $($_.FullName)"
    python -m scripts.ingest_jsonl_all $_.FullName
}

Write-Host "All files ingested."
