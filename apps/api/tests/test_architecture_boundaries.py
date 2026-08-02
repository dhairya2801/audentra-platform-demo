from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "audentra"
FRAMEWORK_NEUTRAL_PACKAGES = (
    "application",
    "contracts",
    "core",
    "domain",
    "infrastructure",
    "integrations",
)


def _python_files(packages: Iterable[str]) -> Iterable[Path]:
    for package in packages:
        yield from (SOURCE_ROOT / package).rglob("*.py")


def _external_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def test_fastapi_is_confined_to_the_http_adapter_and_api_bootstrap() -> None:
    framework_packages = {"fastapi", "starlette"}
    offenders = {
        str(path.relative_to(SOURCE_ROOT)): sorted(imports & framework_packages)
        for path in _python_files(FRAMEWORK_NEUTRAL_PACKAGES)
        if (imports := _external_import_roots(path)) & framework_packages
    }

    assert offenders == {}


def test_application_core_and_domain_do_not_import_database_or_storage_sdks() -> None:
    infrastructure_sdks = {"asyncpg", "boto3", "botocore", "fitz", "sqlalchemy"}
    offenders = {
        str(path.relative_to(SOURCE_ROOT)): sorted(imports & infrastructure_sdks)
        for path in _python_files(("application", "contracts", "core", "domain"))
        if (imports := _external_import_roots(path)) & infrastructure_sdks
    }

    assert offenders == {}
