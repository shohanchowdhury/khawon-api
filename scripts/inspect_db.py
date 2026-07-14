"""Quick Railway/local DB inspection."""
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from database import engine  # noqa: E402


def main() -> None:
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
        ).fetchall()
        print(f"tables: {len(tables)}")
        for name in [
            "restaurants",
            "products",
            "canonical_dishes",
            "product_variations",
            "users",
            "restaurant_reviews",
        ]:
            try:
                n = conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
                print(f"{name}: {n}")
            except Exception as exc:
                print(f"{name}: error ({exc})")
        ext = conn.execute(text("SELECT extname FROM pg_extension ORDER BY 1")).fetchall()
        print("extensions:", [row[0] for row in ext])


if __name__ == "__main__":
    main()
