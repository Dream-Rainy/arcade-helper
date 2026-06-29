from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "arcade_helper"
FORBIDDEN_IMPORT_PREFIXES = (
    "nonebot",
    "nonebot_plugin_",
    "src.plugins.chiffon_bot",
)


def _source_files() -> list[Path]:
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue

        for name in names:
            if any(name == prefix or name.startswith(prefix) for prefix in FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(name)

    return offenders


def test_arcade_helper_has_no_nonebot_or_plugin_imports() -> None:
    offenders = {
        str(path): imports
        for path in _source_files()
        if (imports := _forbidden_imports(path))
    }

    assert offenders == {}


def test_arcade_helper_has_no_forbidden_source_references() -> None:
    forbidden = ("nonebot", "src.plugins.chiffon_bot")
    offenders: list[str] = []

    for path in PACKAGE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(item in text for item in forbidden):
            offenders.append(str(path))

    assert offenders == []


def test_arcade_helper_import_does_not_load_nonebot() -> None:
    code = (
        "import sys; "
        "import arcade_helper; "
        "raise SystemExit(1 if any(name == 'nonebot' or name.startswith('nonebot.') "
        "for name in sys.modules) else 0)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": (
                str((PROJECT_ROOT / "src").resolve())
                if not os.environ.get("PYTHONPATH")
                else f"{PROJECT_ROOT / 'src'}{os.pathsep}{os.environ['PYTHONPATH']}"
            ),
        },
        check=False,
    )

    assert result.returncode == 0


def test_arcade_helper_import_does_not_load_optional_storage_or_sse() -> None:
    code = (
        "import sys; "
        "import arcade_helper; "
        "forbidden = {'tortoise', 'aiohttp'}; "
        "raise SystemExit(1 if any(name in forbidden or name.startswith('tortoise.') "
        "or name.startswith('aiohttp.') for name in sys.modules) else 0)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": (
                str((PROJECT_ROOT / "src").resolve())
                if not os.environ.get("PYTHONPATH")
                else f"{PROJECT_ROOT / 'src'}{os.pathsep}{os.environ['PYTHONPATH']}"
            ),
        },
        check=False,
    )

    assert result.returncode == 0


def test_arcade_helper_package_exports_stable_public_api() -> None:
    import arcade_helper as data

    expected = {
        "AccountNotBoundError",
        "CatalogSyncResult",
        "ArcadeHelperClient",
        "ArcadeHelperError",
        "ClientClosedError",
        "ClientNotStartedError",
        "DataFreshness",
        "DataResult",
        "DefaultAccountNotFoundError",
        "MaimaiPlayer",
        "MatchType",
        "NoProviderAvailableError",
        "OAuthStateMismatchError",
        "PlatformIdentity",
        "ProviderApiResponse",
        "ProviderOperation",
        "ProviderRegistry",
        "ProviderRoutePolicy",
        "SongData",
        "SongQueryResult",
        "SongSearchService",
        "TokenRefreshError",
        "StorageNotConfiguredError",
        "AccountStoreMigrationError",
    }

    assert expected <= set(data.__all__)
    for name in expected:
        assert getattr(data, name) is not None
