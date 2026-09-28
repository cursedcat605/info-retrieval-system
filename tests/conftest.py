"""pytest 公共夹具。

数据库相关测试直接复用线上那份 ``data/db/selects.sqlite``（**只读**打开），
避免为了跑测试重新构建全量数据。若数据库不存在则自动跳过。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database import default_db_path  # noqa: E402
from src.utils.paths import project_root  # noqa: E402


def _db_file() -> Path:
    path = Path(default_db_path())
    return path if path.is_absolute() else project_root() / path


@pytest.fixture(scope="session")
def db_path() -> Path:
    path = _db_file()
    if not path.exists():
        pytest.skip(f"数据库不存在，跳过：{path}")
    return path


@pytest.fixture()
def conn(db_path: Path):
    """以 ``mode=ro`` 打开，保证测试绝不改动线上库。"""
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope="session")
def record_count(db_path: Path) -> int:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return int(connection.execute(
            "SELECT COUNT(*) FROM selects_records").fetchone()[0])
    finally:
        connection.close()
