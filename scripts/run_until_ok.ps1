$teams = @(
    # NIP
    4411,
    # Mouz
    4494,
    # Navi
    4608,
    # Pain
    4773,
    # 3DMax
    4914,
    # Liquid
    5973,
    # G2
    5995,
    # Mongolz
    6248,
    # Astralis
    6665,
    # Faze
    6667,
    # NRG
    6673,
    # Spirit
    7020,
    # Heroic
    7175,
    # BIG
    7532,
    # Furia
    8297,
    # MIBR
    9215,
    # Vitality
    9565,
    # GamerLegion
    9928,
    # B8
    11241,
    # Falcons
    11283,
    # Hotu
    11581,
    # Aurora
    11861,
    # M80
    12376,
    # Betboom
    12394,
    # Passion UA
    12426,
    # Parivision
    12467,
    # Legacy
    12468,
    # BC.Game
    12878,
    # FUT
    13286,
    # Gentle Mates
    13404
)

$script = "scripts/download_matches.py"

$delay = 2

foreach ($team in $teams) {

    Write-Host ""
    Write-Host "=============================="
    Write-Host "Starting team $team"
    Write-Host "=============================="

    $attempt = 1

    while ($true) {

        Write-Host "Team $team - Attempt $attempt"

        python $script --team $team

        if ($LASTEXITCODE -eq 0) {
            Write-Host "Team $team completed successfully"
            break
        }

        Write-Host "Team $team failed. Sleeping $delay sec then retrying..."
        Start-Sleep -Seconds $delay

        $attempt++
    }
}

Write-Host ""
Write-Host "ALL TEAMS COMPLETE"
