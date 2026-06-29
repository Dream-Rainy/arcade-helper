"""Account store migration helpers."""

from __future__ import annotations

from dataclasses import dataclass

from arcade_helper.core.errors import AccountStoreMigrationError

from .dump import AccountStoreConflictPolicy, AccountStoreDump, AccountStoreImportResult, DumpableAccountStore


@dataclass(frozen=True)
class AccountStoreMigrationResult:
    """Result of migrating account binding and OAuth token data."""

    dump: AccountStoreDump
    import_result: AccountStoreImportResult


async def migrate_account_store(
    *,
    source: DumpableAccountStore,
    target: DumpableAccountStore,
    conflict_policy: AccountStoreConflictPolicy = "keep_target",
    dry_run: bool = False,
) -> AccountStoreMigrationResult:
    """Migrate account binding and OAuth token data through a portable dump."""

    if not hasattr(source, "export_account_dump"):
        raise AccountStoreMigrationError("source account store does not support export")
    if not hasattr(target, "import_account_dump"):
        raise AccountStoreMigrationError("target account store does not support import")

    dump = await source.export_account_dump()
    import_result = await target.import_account_dump(
        dump,
        conflict_policy=conflict_policy,
        dry_run=dry_run,
    )
    return AccountStoreMigrationResult(dump=dump, import_result=import_result)


__all__ = ["AccountStoreMigrationResult", "migrate_account_store"]
