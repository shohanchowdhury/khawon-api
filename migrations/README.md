# Migrations

`schema.sql` is the source of truth and builds a **fresh** database. These
numbered files bring an **existing** database up to the same shape.

There is no Alembic here on purpose: the schema uses Postgres-native features
the ORM cannot express, and the catalogue is re-derivable from the pipeline.
But "just reset and reload" stops being safe once real users and reviews
exist, so schema changes get a migration file from now on.

Apply in numeric order, once per database:

    psql "$DATABASE_PUBLIC_URL" -f migrations/001_products_normalized_name.sql

Every file must be idempotent (`IF NOT EXISTS`) so a re-run is a no-op.
When adding a column, add it to BOTH schema.sql and a new migration file.

| File | What it does |
|------|--------------|
| `001_products_normalized_name.sql` | Adds `products.normalized_name` + index for read-time brand grouping |

Related: `schema_geo.sql` is NOT a migration — it is an optional add-on that
requires PostGIS (absent on Railway) and is applied only when building the
"near me" feature.
