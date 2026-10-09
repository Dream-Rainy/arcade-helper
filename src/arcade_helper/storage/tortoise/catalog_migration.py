"""Explicit additive setup and repeatable legacy identity backfill."""

from tortoise import Tortoise

from arcade_helper.catalog.importer import prepare_native_catalog
from arcade_helper.core.errors import StorageNotConfiguredError
from .catalog_store import TortoiseCatalogStore
from .catalog_models import AliasRevision
from .song_store import TortoiseSongStore


async def upgrade_catalog_schema(*, connection_name: str = "default", dry_run: bool = True) -> list[str]:
    """Explicit additive S1→S2/S3 migration; back up the database before applying."""
    db = Tortoise.get_connection(connection_name)
    dialect = db.capabilities.dialect
    if dialect not in {"sqlite", "postgres", "mysql"}:
        raise ValueError(f"Unsupported migration backend: {dialect}")
    quote = "`" if dialect == "mysql" else '"'
    json_type = "JSONB" if dialect == "postgres" else "JSON"
    additions = {
        "music_works": {"version": "INT NOT NULL DEFAULT 1", "redirect_to": "UUID" if dialect == "postgres" else "CHAR(36)"},
        "game_songs": {"version": "INT NOT NULL DEFAULT 1", "edition_key": "VARCHAR(64) NOT NULL DEFAULT ''"},
        "catalog_imports": {"observed_payload": json_type, "resolutions": json_type},
    }
    statements = []
    for table, columns in additions.items():
        if dialect == "sqlite":
            existing = {r["name"] for r in await db.execute_query_dict(f'PRAGMA table_info("{table}")')}
        else:
            schema = "current_schema()" if dialect == "postgres" else "DATABASE()"
            placeholder = "$1" if dialect == "postgres" else "%s"
            existing = {r["column_name"] for r in await db.execute_query_dict(
                f"SELECT column_name AS column_name FROM information_schema.columns WHERE table_schema={schema} AND table_name={placeholder}", [table])}
        if not existing:
            continue
        for column, sql_type in columns.items():
            if column not in existing:
                statements.append(f"ALTER TABLE {quote}{table}{quote} ADD COLUMN {quote}{column}{quote} {sql_type}")
    index_name = "idx_alias_evidence_key_kind"
    if dialect == "sqlite":
        columns = await db.execute_query_dict('PRAGMA table_info("alias_evidence")')
        indexes = await db.execute_query_dict('PRAGMA index_list("alias_evidence")')
        indexed = any(row["name"] == index_name for row in indexes)
    else:
        schema = "current_schema()" if dialect == "postgres" else "DATABASE()"
        columns = await db.execute_query_dict(
            f"SELECT column_name FROM information_schema.columns WHERE table_schema={schema} AND table_name='alias_evidence'")
        if dialect == "postgres":
            indexes = await db.execute_query_dict(
                "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND tablename='alias_evidence'")
            indexed = any(row["indexname"] == index_name for row in indexes)
        else:
            indexes = await db.execute_query_dict(
                "SELECT index_name FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='alias_evidence'")
            indexed = any(row["index_name"] == index_name for row in indexes)
    if columns and not indexed:
        # MySQL requires a prefix for TEXT. Equality still checks the complete key.
        key = f"{quote}alias_key{quote}" + ("(191)" if dialect == "mysql" else "")
        statements.append(f"CREATE INDEX {quote}{index_name}{quote} ON {quote}alias_evidence{quote} ({key}, {quote}kind{quote})")
    if not dry_run:
        # MySQL DDL is not transactionally reversible; every step is independently repeatable.
        for statement in statements:
            await db.execute_query(statement)
        if statements:
            await db.execute_query("UPDATE catalog_imports SET resolutions='{}' WHERE resolutions IS NULL")
    return statements


async def initialize_catalog_storage(*, connection_name: str = "default") -> None:
    """Create missing tables after host-owned Tortoise.init; never run on import."""
    if Tortoise.apps.get("models", {}).get("AliasRevision") is not AliasRevision:
        raise StorageNotConfiguredError("Register CATALOG_MODEL_MODULE under models before catalog initialization")
    await Tortoise.generate_schemas(safe=True)
    await AliasRevision.get_or_create(id=1, using_db=Tortoise.get_connection(connection_name))
    # Row 1 is the public catalog generation; row 2 only serializes pending-request management.
    await AliasRevision.get_or_create(id=2, using_db=Tortoise.get_connection(connection_name))


async def migrate_catalog_identities(
    *, store: TortoiseCatalogStore | None = None, song_store=None, dry_run: bool = True,
    games: tuple[str, ...] = ("maimai", "chunithm"),
) -> dict:
    """Backfill native IDs without modifying the existing songs or alias tables.

    ``catalog:native`` names the legacy aggregate, not an alias's real provider.
    The caller must initialize the new tables first, even for a dry run.
    """
    store = store or TortoiseCatalogStore()
    source = song_store or TortoiseSongStore()
    reports = {}
    for game in ("maimai", "chunithm"):
        if game not in games:
            continue
        songs = await source.load_all_songs(game, strict=True) if isinstance(source, TortoiseSongStore) else await source.load_all_songs(game)
        records, invalid = prepare_native_catalog(game, songs)
        reports[game] = await store.import_records(
            game, records, provider="catalog", namespace="native", dry_run=dry_run,
            complete=False, invalid=invalid,
        )
    return reports
