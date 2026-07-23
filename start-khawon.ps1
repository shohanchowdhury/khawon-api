<#
.SYNOPSIS
    Set up and run the local Khawon stack: Postgres, the API, and the web app.

.DESCRIPTION
    Khawon's database is a DEDICATED local Postgres cluster on port 5433 -- not
    a system-wide PostgreSQL service. It uses trust auth on localhost, so there
    is no password to share or lose. It is deliberately not a Windows service,
    which is why this script exists.

    FIRST TIME on a machine, run with -Setup. That creates the cluster, applies
    schema.sql and every migration, installs Python and Node dependencies,
    writes a .env, and loads the catalogue if you point it at seed data.

    After that, just run the script with no arguments.

.PARAMETER Setup
    One-time setup. Safe to re-run: existing pieces are detected and skipped.

.PARAMETER Seed
    Where the catalogue comes from. Either:
      * a .dump file  -> restored with pg_restore (fast, ~1.3 MB, recommended)
      * a directory   -> the pipeline's v2_output, loaded with load_batch.py
    If omitted, -Setup looks for khawon-seed.dump next to this script, and
    otherwise leaves you with an empty but working schema.

.PARAMETER DbOnly
    Start Postgres and stop there -- for running the dev servers yourself.

.PARAMETER Stop
    Shut the Postgres cluster down. Dev servers are separate; close their windows.

.EXAMPLE
    .\start-khawon.ps1 -Setup -Seed .\khawon-seed.dump
    .\start-khawon.ps1
    .\start-khawon.ps1 -DbOnly
    .\start-khawon.ps1 -Stop

.NOTES
    Override the data directory with the KHAWON_PGDATA environment variable,
    and the Postgres install with KHAWON_PGBIN.
#>
[CmdletBinding()]
param(
    [switch]$Setup,
    [string]$Seed,
    [switch]$DbOnly,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'

# This script lives in khawon-api and expects khawon-web as a SIBLING:
#   Khawon/
#     khawon-api/   <- you are here
#     khawon-web/
$Api     = $PSScriptRoot
$Root    = Split-Path -Parent $PSScriptRoot
$PgPort  = 5433
$ApiPort = 8000
$WebPort = 5173
$DbName  = 'khawon'
$DbUser  = 'khawon'

# Data directory: overridable, but defaults somewhere every Windows user has.
$PgData = if ($env:KHAWON_PGDATA) { $env:KHAWON_PGDATA }
          else { Join-Path $env:LOCALAPPDATA 'khawon-pgdata' }

# PowerShell 5.1 turns ANY native-exe stderr output into a NativeCommandError
# ErrorRecord, and with $ErrorActionPreference='Stop' that becomes fatal even
# when the exe exited 0. psql writes NOTICEs to stderr, so idempotent
# migrations would "fail". Run native tools through this and trust $LASTEXITCODE.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [string]$What, [switch]$IgnoreExit)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { $_.ToString() } | Out-Null
        if (-not $IgnoreExit -and $LASTEXITCODE -ne 0) {
            throw "$What failed (exit code $LASTEXITCODE)"
        }
    } finally { $ErrorActionPreference = $prev }
}

# Silences psql NOTICEs at the source, so idempotent DDL is quiet.
$env:PGOPTIONS = '--client-min-messages=warning'

function Write-Step {
    param([string]$Label, [string]$Message, [string]$Color = 'Gray')
    Write-Host ("{0,-10}" -f $Label) -ForegroundColor DarkGray -NoNewline
    Write-Host $Message -ForegroundColor $Color
}

function Find-PgBin {
    if ($env:KHAWON_PGBIN) { return $env:KHAWON_PGBIN }
    $onPath = Get-Command pg_ctl.exe -ErrorAction SilentlyContinue
    if ($onPath) { return Split-Path -Parent $onPath.Source }
    $candidates = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\pg_ctl.exe' -ErrorAction SilentlyContinue |
                  Sort-Object { [int]($_.Directory.Parent.Name) } -Descending
    if ($candidates) { return $candidates[0].Directory.FullName }
    throw "PostgreSQL not found. Install it, or set KHAWON_PGBIN to the folder containing pg_ctl.exe."
}

function Test-Port {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try   { $client.Connect('localhost', $Port); return $true }
    catch { return $false }
    finally { $client.Dispose() }
}

function Start-Cluster {
    param([string]$PgBin)
    if (Test-Port $PgPort) { Write-Step 'postgres' "already running on $PgPort"; return }

    Write-Host ("{0,-10}" -f 'postgres') -ForegroundColor DarkGray -NoNewline
    Write-Host "starting on $PgPort" -NoNewline

    # pg_ctl blocks its caller on Windows even with -l, so launch it detached
    # and poll the port. Do NOT "fix" this into -Wait.
    Start-Process -FilePath (Join-Path $PgBin 'pg_ctl.exe') `
        -ArgumentList "-D `"$PgData`"", "-l `"$PgData\server.log`"", "-o `"-p $PgPort`"", 'start' `
        -WindowStyle Hidden

    foreach ($i in 1..20) {
        Start-Sleep -Milliseconds 500
        if (Test-Port $PgPort) { Write-Host ' ok' -ForegroundColor Green; return }
        Write-Host '.' -NoNewline
    }
    Write-Host ' FAILED' -ForegroundColor Red
    Write-Host "          see $PgData\server.log" -ForegroundColor DarkGray
    exit 1
}

$PgBin = Find-PgBin
$Web   = Join-Path $Root 'khawon-web'

# ---------------------------------------------------------------- stop mode --
if ($Stop) {
    if (-not (Test-Port $PgPort)) { Write-Step 'postgres' "not running on $PgPort"; return }
    & (Join-Path $PgBin 'pg_ctl.exe') -D $PgData -m fast stop | Out-Null
    Start-Sleep -Seconds 1
    if (Test-Port $PgPort) { Write-Step 'postgres' 'failed to stop' 'Red' }
    else                   { Write-Step 'postgres' 'stopped' 'Green' }
    return
}

# --------------------------------------------------------------- setup mode --
if ($Setup) {
    Write-Host "`nKhawon setup" -ForegroundColor Cyan
    Write-Step 'using' "postgres at $PgBin"
    Write-Step 'using' "data dir $PgData"
    Write-Host ''

    # 1. cluster
    if (Test-Path (Join-Path $PgData 'PG_VERSION')) {
        Write-Step 'cluster' 'already exists, keeping it'
    } else {
        Write-Step 'cluster' 'creating (trust auth, no password)'
        Invoke-Native (Join-Path $PgBin 'initdb.exe') @(
            '-D',$PgData,'-U',$DbUser,'--auth-local=trust','--auth-host=trust','-E','UTF8'
        ) 'initdb' 
    }

    Start-Cluster -PgBin $PgBin

    # 2. database
    $exists = & (Join-Path $PgBin 'psql.exe') -h localhost -p $PgPort -U $DbUser -d postgres -A -t `
        -c "select 1 from pg_database where datname='$DbName'"
    if ($exists -match '1') {
        Write-Step 'database' "$DbName already exists, keeping it"
    } else {
        Invoke-Native (Join-Path $PgBin 'createdb.exe') @(
            '-h','localhost','-p',"$PgPort",'-U',$DbUser,$DbName
        ) 'createdb' 
        Write-Step 'database' "$DbName created" 'Green'

        Write-Step 'schema' 'applying schema.sql'
        Invoke-Native (Join-Path $PgBin 'psql.exe') @(
            '-h','localhost','-p',"$PgPort",'-U',$DbUser,'-d',$DbName,
            '-v','ON_ERROR_STOP=1','-q','-f',(Join-Path $Api 'schema.sql')
        ) 'schema.sql' 
    }

    # 3. migrations -- idempotent, so run them every time
    Get-ChildItem (Join-Path $Api 'migrations\*.sql') | Sort-Object Name | ForEach-Object {
        Invoke-Native (Join-Path $PgBin 'psql.exe') @(
            '-h','localhost','-p',"$PgPort",'-U',$DbUser,'-d',$DbName,
            '-v','ON_ERROR_STOP=1','-q','-f',$_.FullName
        ) $_.Name
        Write-Step 'migration' $_.Name
    }

    # 4. .env -- never overwrite one that already exists, it may hold real keys
    $envPath = Join-Path $Api '.env'
    if (Test-Path $envPath) {
        Write-Step 'env' '.env exists, leaving it alone'
    } else {
        $secret = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
        @(
            '# Written by start-khawon.ps1 -Setup',
            "DATABASE_URL=postgresql://$DbUser@localhost:$PgPort/$DbName",
            "JWT_SECRET=$secret",
            '',
            '# Optional third-party keys. Image upload, Google Places and AI photo',
            '# generation stay disabled until these are filled in; nothing else breaks.',
            '# CLOUDINARY_CLOUD_NAME=',
            '# CLOUDINARY_API_KEY=',
            '# CLOUDINARY_API_SECRET=',
            '# GOOGLE_PLACES_API_KEY=',
            '# HF_TOKEN='
        ) | Set-Content -Path $envPath -Encoding utf8
        Write-Step 'env' '.env written' 'Green'
    }

    # 5. python deps
    $venvPython = Join-Path $Api '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Step 'python' 'creating .venv'
        Push-Location $Api; & python -m venv .venv; Pop-Location
    }
    Write-Step 'python' 'installing requirements'
    & $venvPython -m pip install -q -r (Join-Path $Api 'requirements.txt')

    # 6. node deps -- khawon-web is a separate repo and may not be cloned yet
    if (-not (Test-Path $Web)) {
        Write-Step 'node' "no khawon-web beside khawon-api -- skipping" 'Yellow'
        Write-Host  "          Clone it next to this repo to run the frontend." -ForegroundColor DarkGray
    } elseif (Test-Path (Join-Path $Web 'node_modules')) {
        Write-Step 'node' 'node_modules exists, skipping'
    } else {
        Write-Step 'node' 'npm install'
        Push-Location $Web; & npm install --silent; Pop-Location
    }

    # 7. seed
    if (-not $Seed) {
        $default = Join-Path $Root 'khawon-seed.dump'
        if (Test-Path $default) { $Seed = $default }
    }

    $rowCount = & (Join-Path $PgBin 'psql.exe') -h localhost -p $PgPort -U $DbUser -d $DbName -A -t `
        -c 'select count(*) from restaurants'
    if ([int]$rowCount -gt 0) {
        Write-Step 'data' "$rowCount restaurants already loaded, skipping seed"
    }
    elseif (-not $Seed) {
        Write-Step 'data' 'no seed provided -- schema is empty' 'Yellow'
        Write-Host  "          Ask for khawon-seed.dump, then:" -ForegroundColor DarkGray
        Write-Host  "          .\start-khawon.ps1 -Setup -Seed .\khawon-seed.dump" -ForegroundColor DarkGray
    }
    elseif ($Seed -like '*.dump') {
        Write-Step 'data' "restoring $(Split-Path -Leaf $Seed)"
        Invoke-Native (Join-Path $PgBin 'pg_restore.exe') @(
            '-h','localhost','-p',"$PgPort",'-U',$DbUser,'-d',$DbName,
            '--no-owner','--clean','--if-exists',$Seed
        ) 'pg_restore' -IgnoreExit
        $n = & (Join-Path $PgBin 'psql.exe') -h localhost -p $PgPort -U $DbUser -d $DbName -A -t `
            -c 'select count(*) from restaurants'
        Write-Step 'data' "$n restaurants restored" 'Green'
    }
    else {
        # A directory: the pipeline's v2_output. Native Windows paths matter --
        # Git Bash does not convert the glob string, and a zero-match glob makes
        # load_batch report success while loading nothing.
        $d = (Resolve-Path $Seed).Path -replace '\\', '/'
        Write-Step 'data' "loading from $d"
        Push-Location $Api
        & $venvPython load_batch.py "$d/consolidated.json" "$d/canonical_dishes.json" `
            "$d/restaurants_*_restaurants.json" --chains "$d/chains.json"
        Pop-Location
        $n = & (Join-Path $PgBin 'psql.exe') -h localhost -p $PgPort -U $DbUser -d $DbName -A -t `
            -c 'select count(*) from restaurants'
        if ([int]$n -eq 0) { Write-Step 'data' 'LOADED NOTHING -- check the seed path' 'Red'; exit 1 }
        Write-Step 'data' "$n restaurants loaded" 'Green'
    }

    Write-Host "`nSetup complete. Run .\start-khawon.ps1 to start everything.`n" -ForegroundColor Green
    return
}

# ----------------------------------------------------------------- run mode --
if (-not (Test-Path (Join-Path $PgData 'PG_VERSION'))) {
    Write-Step 'postgres' "no cluster at $PgData" 'Red'
    Write-Host  "          First time here? Run: .\start-khawon.ps1 -Setup" -ForegroundColor DarkGray
    exit 1
}

Start-Cluster -PgBin $PgBin

if ($DbOnly) {
    Write-Host ''
    Write-Step 'ready' "postgresql://$DbUser@localhost:$PgPort/$DbName" 'Cyan'
    return
}

# api
if (Test-Port $ApiPort) {
    Write-Step 'api' "something is already on $ApiPort -- skipped" 'Yellow'
} else {
    $venvPython = Join-Path $Api '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Step 'api' 'no .venv -- run: .\start-khawon.ps1 -Setup' 'Red'
        exit 1
    }
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-Command',
        "Set-Location '$Api'; .\.venv\Scripts\python.exe -m uvicorn main:app --reload --port $ApiPort"
    )
    Write-Step 'api' "http://localhost:$ApiPort (own window, --reload)" 'Green'
}

# web -- optional; the API is useful on its own
if (-not (Test-Path $Web)) {
    Write-Step 'web' 'khawon-web not cloned beside khawon-api -- skipped' 'Yellow'
    return
}
if (-not (Test-Path (Join-Path $Web 'node_modules'))) {
    Write-Step 'web' 'no node_modules -- run: .\start-khawon.ps1 -Setup' 'Red'
    exit 1
}
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command', "Set-Location '$Web'; npm run dev"
)
Write-Step 'web' "http://localhost:$WebPort (own window)" 'Green'

Write-Host ''
Write-Step 'note' 'closing those windows stops the dev servers, not the database' 'DarkGray'
Write-Step 'note' 'run with -Stop to shut the database down' 'DarkGray'
