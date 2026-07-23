# Getting Khawon running locally

Windows. Should take about ten minutes, most of it downloads.

## What you need first

1. **PostgreSQL 17 or 18** — <https://www.postgresql.org/download/windows/>.
   During install it asks for a `postgres` superuser password. Set one and
   write it down, but you will not need it here: Khawon runs its own separate
   database cluster with no password at all. Just don't skip the install, since
   we use its tools.
2. **Python 3.11+** and **Node 18+**.
3. **`khawon-seed.dump`** — ask Shohan. It's about 1.3 MB. This is the
   catalogue; it isn't in git.

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

| Command | Does |
|---|---|
| `.\start-khawon.ps1` | database + API + web |
| `.\start-khawon.ps1 -DbOnly` | just the database, run the servers yourself |
| `.\start-khawon.ps1 -Stop` | shut the database down |

## Things that will confuse you otherwise

**The database is on port 5433, not 5432.** Khawon uses its own cluster in
`%LOCALAPPDATA%\khawon-pgdata`, deliberately separate from the system
PostgreSQL service you installed. If you point pgAdmin at 5432 you'll find an
empty database and think something broke. Connect to:

| | |
|---|---|
| Host | `localhost` |
| Port | **5433** |
| Database | `khawon` |
| User | `khawon` |
| Password | *(leave blank — trust auth)* |

**It doesn't survive a reboot.** The cluster isn't a Windows service, so after
restarting your machine you must run the script again before anything works.
The symptom if you forget: the API starts fine and then every request fails
with a connection error, which looks like broken code but isn't.

**Some features stay off.** Setup writes a `.env` without the Cloudinary,
Google Places and Hugging Face keys — those are Shohan's. Image upload, place
lookup and AI photo generation are disabled without them. Nothing else is
affected.

## Check it worked

```powershell
.\.venv\Scripts\python.exe -m pytest -q
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
