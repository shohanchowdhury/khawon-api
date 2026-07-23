#!/usr/bin/env bash
#
# start-khawon.sh -- macOS / Linux equivalent of start-khawon.ps1.
#
# Khawon's database is a DEDICATED Postgres cluster on port 5433, not a
# system-wide service. It uses trust auth on localhost, so there is no password
# to share or lose. It is not registered with launchd/systemd, which is why
# this script exists.
#
#   ./start-khawon.sh --setup     first time on a machine
#   ./start-khawon.sh             database + API + web
#   ./start-khawon.sh --db-only   just the database
#   ./start-khawon.sh --stop      shut the database down
#   ./start-khawon.sh --dump      regenerate the committed seed (maintainers)
#
# Override the data directory with KHAWON_PGDATA, the Postgres install with
# KHAWON_PGBIN.

set -euo pipefail

API="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$API")"
WEB="$ROOT/khawon-web"

PGPORT=5433
API_PORT=8000
WEB_PORT=5173
DB_NAME=khawon
DB_USER=khawon

PGDATA="${KHAWON_PGDATA:-$HOME/.local/share/khawon-pgdata}"

# Quiet psql NOTICEs so idempotent DDL does not look alarming.
export PGOPTIONS='--client-min-messages=warning'

MODE=run
SEED=""
while [ $# -gt 0 ]; do
    case "$1" in
        --setup)   MODE=setup ;;
        --db-only) MODE=dbonly ;;
        --stop)    MODE=stop ;;
        --dump)    MODE=dump ;;
        --seed)    SEED="${2:-}"; shift ;;
        -h|--help) sed -n '3,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

step() { printf '%-10s%s\n' "$1" "$2"; }
die()  { printf '%-10s%s\n' "$1" "$2" >&2; exit 1; }

# --- locate the Postgres tools -------------------------------------------
find_pgbin() {
    if [ -n "${KHAWON_PGBIN:-}" ]; then echo "$KHAWON_PGBIN"; return; fi
    if command -v pg_ctl >/dev/null 2>&1; then dirname "$(command -v pg_ctl)"; return; fi
    # Homebrew (Apple silicon, then Intel), then Debian/Ubuntu layout. Highest
    # version last so the newest wins.
    local candidate found=""
    for candidate in /opt/homebrew/opt/postgresql@*/bin \
                     /usr/local/opt/postgresql@*/bin \
                     /usr/lib/postgresql/*/bin; do
        [ -x "$candidate/pg_ctl" ] && found="$candidate"
    done
    if [ -n "${found:-}" ]; then echo "$found"; return; fi
    die 'postgres' 'not found. Install it, or set KHAWON_PGBIN to the folder containing pg_ctl.'
}

PGBIN="$(find_pgbin)"

port_open() {
    # bash's /dev/tcp is not in POSIX sh but is fine here, and avoids depending
    # on nc/lsof being installed.
    (exec 3<>"/dev/tcp/localhost/$1") >/dev/null 2>&1
}

start_cluster() {
    if port_open "$PGPORT"; then step postgres "already running on $PGPORT"; return; fi
    # Unlike Windows, pg_ctl -w behaves here: it waits and reports properly.
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -o "-p $PGPORT" -w start >/dev/null \
        || die postgres "failed to start -- see $PGDATA/server.log"
    step postgres "started on $PGPORT"
}

psql_db() { "$PGBIN/psql" -h localhost -p "$PGPORT" -U "$DB_USER" -d "$DB_NAME" "$@"; }

# --------------------------------------------------------------------- stop --
if [ "$MODE" = stop ]; then
    port_open "$PGPORT" || { step postgres "not running on $PGPORT"; exit 0; }
    "$PGBIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null
    step postgres 'stopped'
    exit 0
fi

# --------------------------------------------------------------------- dump --
if [ "$MODE" = dump ]; then
    port_open "$PGPORT" || die postgres 'not running -- start it first'
    mkdir -p "$API/seed"
    "$PGBIN/pg_dump" -h localhost -p "$PGPORT" -U "$DB_USER" -d "$DB_NAME" \
        -Fc -Z9 -f "$API/seed/khawon-seed.dump"
    step dump "$(du -h "$API/seed/khawon-seed.dump" | cut -f1) -- commit it so new clones get this data"
    exit 0
fi

# -------------------------------------------------------------------- setup --
if [ "$MODE" = setup ]; then
    echo
    echo "Khawon setup"
    step using "postgres at $PGBIN"
    step using "data dir $PGDATA"
    echo

    if [ -f "$PGDATA/PG_VERSION" ]; then
        step cluster 'already exists, keeping it'
    else
        step cluster 'creating (trust auth, no password)'
        mkdir -p "$(dirname "$PGDATA")"
        "$PGBIN/initdb" -D "$PGDATA" -U "$DB_USER" \
            --auth-local=trust --auth-host=trust -E UTF8 >/dev/null
    fi

    start_cluster

    if "$PGBIN/psql" -h localhost -p "$PGPORT" -U "$DB_USER" -d postgres -Atc \
        "select 1 from pg_database where datname='$DB_NAME'" | grep -q 1; then
        step database "$DB_NAME already exists, keeping it"
    else
        "$PGBIN/createdb" -h localhost -p "$PGPORT" -U "$DB_USER" "$DB_NAME"
        step database "$DB_NAME created"
        step schema 'applying schema.sql'
        psql_db -v ON_ERROR_STOP=1 -q -f "$API/schema.sql"
    fi

    # Migrations are idempotent, so run them every time.
    for migration in "$API"/migrations/*.sql; do
        psql_db -v ON_ERROR_STOP=1 -q -f "$migration"
        step migration "$(basename "$migration")"
    done

    if [ -f "$API/.env" ]; then
        step env '.env exists, leaving it alone'
    else
        secret="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 40)"
        cat > "$API/.env" <<ENVEOF
# Written by start-khawon.sh --setup
DATABASE_URL=postgresql://$DB_USER@localhost:$PGPORT/$DB_NAME
JWT_SECRET=$secret

# Optional third-party keys. Image upload, Google Places and AI photo
# generation stay disabled until these are filled in; nothing else breaks.
# CLOUDINARY_CLOUD_NAME=
# CLOUDINARY_API_KEY=
# CLOUDINARY_API_SECRET=
# GOOGLE_PLACES_API_KEY=
# HF_TOKEN=
ENVEOF
        step env '.env written'
    fi

    # Note .venv/bin, not .venv/Scripts -- the Windows layout differs.
    if [ ! -x "$API/.venv/bin/python" ]; then
        step python 'creating .venv'
        (cd "$API" && python3 -m venv .venv)
    fi
    step python 'installing requirements'
    "$API/.venv/bin/python" -m pip install -q -r "$API/requirements.txt"

    if [ ! -d "$WEB" ]; then
        step node "no khawon-web beside khawon-api -- skipping"
    elif [ -d "$WEB/node_modules" ]; then
        step node 'node_modules exists, skipping'
    else
        step node 'npm install'
        (cd "$WEB" && npm install --silent)
    fi

    [ -n "$SEED" ] || { [ -f "$API/seed/khawon-seed.dump" ] && SEED="$API/seed/khawon-seed.dump"; }

    rows="$(psql_db -Atc 'select count(*) from restaurants')"
    if [ "$rows" -gt 0 ]; then
        step data "$rows restaurants already loaded, skipping seed"
    elif [ -z "$SEED" ]; then
        step data 'no seed found -- schema is empty'
    elif [ "${SEED##*.}" = dump ]; then
        step data "restoring $(basename "$SEED")"
        "$PGBIN/pg_restore" -h localhost -p "$PGPORT" -U "$DB_USER" -d "$DB_NAME" \
            --no-owner --clean --if-exists "$SEED" 2>/dev/null || true
        step data "$(psql_db -Atc 'select count(*) from restaurants') restaurants restored"
    else
        step data "loading from $SEED"
        (cd "$API" && "$API/.venv/bin/python" load_batch.py \
            "$SEED/consolidated.json" "$SEED/canonical_dishes.json" \
            "$SEED/restaurants_*_restaurants.json" --chains "$SEED/chains.json")
        n="$(psql_db -Atc 'select count(*) from restaurants')"
        [ "$n" -gt 0 ] || die data 'LOADED NOTHING -- check the seed path'
        step data "$n restaurants loaded"
    fi

    echo
    echo "Setup complete. Run ./start-khawon.sh to start everything."
    echo
    exit 0
fi

# ----------------------------------------------------------------------- run --
[ -f "$PGDATA/PG_VERSION" ] || die postgres "no cluster at $PGDATA -- run: ./start-khawon.sh --setup"

start_cluster

if [ "$MODE" = dbonly ]; then
    echo
    step ready "postgresql://$DB_USER@localhost:$PGPORT/$DB_NAME"
    exit 0
fi

# Windows opens a window per server; there is no portable equivalent here, so
# both run in the background with logs and are killed together on Ctrl+C.
pids=()
cleanup() {
    echo
    step stopping 'dev servers (database left running)'
    for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup INT TERM

if port_open "$API_PORT"; then
    step api "something is already on $API_PORT -- skipped"
else
    [ -x "$API/.venv/bin/python" ] || die api 'no .venv -- run: ./start-khawon.sh --setup'
    "$API/.venv/bin/python" -m uvicorn main:app --reload --port "$API_PORT" \
        >"$API/.uvicorn.log" 2>&1 &
    pids+=($!)
    step api "http://localhost:$API_PORT  (log: khawon-api/.uvicorn.log)"
fi

if [ ! -d "$WEB" ]; then
    step web 'khawon-web not cloned beside khawon-api -- skipped'
elif [ ! -d "$WEB/node_modules" ]; then
    die web 'no node_modules -- run: ./start-khawon.sh --setup'
else
    (cd "$WEB" && npm run dev >"$WEB/.vite.log" 2>&1) &
    pids+=($!)
    step web "http://localhost:$WEB_PORT  (log: khawon-web/.vite.log)"
fi

echo
step note 'Ctrl+C stops the dev servers; the database keeps running'
step note 'run with --stop to shut the database down'
wait
