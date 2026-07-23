# Getting Khawon running locally

About ten minutes, most of it downloads. Windows and macOS/Linux both covered —
the two scripts do the same things, so read whichever matches your machine.

## What you need first

**Linux (Debian / Ubuntu)** — one line, and read the note below it:

```bash
sudo apt install postgresql postgresql-contrib python3-venv python3-pip nodejs npm
```

`postgresql-contrib` is **not optional**: `pg_trgm` lives there, `schema.sql`
requires it, and without it setup fails at the schema step. `python3-venv` is
likewise a separate package on Debian/Ubuntu — without it `python3 -m venv`
fails with a confusing message about `ensurepip`.

Fedora: `sudo dnf install postgresql-server postgresql-contrib python3 nodejs`.
Arch: `sudo pacman -S postgresql python nodejs npm`.

You do **not** need to start or enable the system `postgresql` service — Khawon
runs its own cluster. And do **not** run the setup script with `sudo`: Postgres
refuses to run as root, and the cluster belongs in your home directory.

**Windows** — install PostgreSQL 17 or 18 from
<https://www.postgresql.org/download/windows/>. It asks for a `postgres`
superuser password; set one, but you won't need it here, since Khawon runs its
own cluster with no password. Don't skip the install, we use its CLI tools.
Then install Python 3.11+ and Node 18+.

**macOS** — `brew install postgresql@18 python@3.12 node`.
That's it. The catalogue ships **inside this repo** (`seed/khawon-seed.dump`,
about 1.2 MB), so there's no data file to chase down.

## Clone both repos side by side

The layout matters — the start script looks for `khawon-web` next to `khawon-api`:

```
Khawon/
  khawon-api/
  khawon-web/
```

```powershell
mkdir Khawon; cd Khawon
git clone https://github.com/shohanchowdhury/khawon-api.git
git clone https://github.com/shohanchowdhury/khawon-web.git
```

## Run setup once

Put `khawon-seed.dump` somewhere handy, then:

```powershell
cd khawon-api
.\start-khawon.ps1 -Setup -Seed C:\path\to\khawon-seed.dump
```

That creates the database cluster, applies the schema and every migration,
writes a `.env`, installs Python and Node dependencies, and restores the
catalogue. It's safe to re-run — anything already done is detected and skipped.

Expect it to finish with `451 restaurants restored`.

If PowerShell refuses to run the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-khawon.ps1 -Setup -Seed C:\path\to\khawon-seed.dump
```

## Every day after that

```powershell
.\start-khawon.ps1
```

Opens two windows — the API on <http://localhost:8000> and the web app on
<http://localhost:5173>. Go to the web app.

| Windows | macOS / Linux | Does |
|---|---|---|
| `.\start-khawon.ps1` | `./start-khawon.sh` | database + API + web |
| `-DbOnly` | `--db-only` | just the database, run the servers yourself |
| `-Stop` | `--stop` | shut the database down |
| `-Dump` | `--dump` | regenerate the committed seed (maintainers) |

On Windows each dev server opens its own window. On macOS/Linux they run in the
background with logs at `khawon-api/.uvicorn.log` and `khawon-web/.vite.log`;
Ctrl+C stops both and leaves the database running.

## Keeping the seed current

`seed/khawon-seed.dump` is a snapshot, not a live feed. After a pipeline reload
adds or changes data, refresh it and commit so everyone else picks it up:

```powershell
.\start-khawon.ps1 -Dump
git add seed/khawon-seed.dump
git commit -m "chore: refresh seed catalogue"
```

Ad-hoc `.dump` files elsewhere are gitignored on purpose — only this one is
tracked.

## Things that will confuse you otherwise

**The database is on port 5433, not 5432.** Khawon uses its own cluster in
`%LOCALAPPDATA%\khawon-pgdata` (Windows) or `~/.local/share/khawon-pgdata` (macOS/Linux), deliberately separate from the system
PostgreSQL service you installed. If you point pgAdmin at 5432 you'll find an
empty database and think something broke. Connect to:

| | |
|---|---|
| Host | `localhost` |
| Port | **5433** |
| Database | `khawon` |
| User | `khawon` |
| Password | *(leave blank — trust auth)* |

**It doesn't survive a reboot.** The cluster isn't registered as a service, so after
restarting your machine you must run the script again before anything works.
The symptom if you forget: the API starts fine and then every request fails
with a connection error, which looks like broken code but isn't.

**Some features stay off.** Setup writes a `.env` without the Cloudinary,
Google Places and Hugging Face keys — those are Shohan's. Image upload, place
lookup and AI photo generation are disabled without them. Nothing else is
affected.

## Check it worked

```powershell
.\.venv\Scripts\python.exe -m pytest -q     # Windows
./.venv/bin/python -m pytest -q             # macOS / Linux
```

94 tests should pass. The suite creates and drops its own `khawon_test`
database and never touches your catalogue.

## Where to read next

- [`PRIMER.md`](PRIMER.md) — five-minute orientation. Start here.
- [`SCHEMA.md`](SCHEMA.md) — ER diagrams, every table and column, real examples.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the full reference.
- [`HANDOFF.md`](HANDOFF.md) — how it's built and where the traps are. §9 in
  particular; those cost real debugging time.

One thing worth knowing on day one: in this codebase **"restaurant" means
*brand*, not location**. A branch is a `restaurants` row; the brand is a
`restaurant_chains` row, and the API is keyed on brand slugs. `SCHEMA.md` §1
explains why in a couple of minutes and will save you an afternoon.
