"""SQLite 表结构定义（含 FTS5 全文索引）。

设计要点
--------
* **append-only 原始层 vs 可重建的派生层**：本库属于派生层，任何时候都可以用
  ``scripts/build_database.py`` 从 ``data/interim/ocr_text`` 全量重建。
* **FTS5 + trigram**：中文没有词边界，用 trigram 分词器可以直接做子串匹配
  （"鹿原镇" 能命中 "…炎陵县鹿原镇…"），比 unicode61 更贴合检索需求。
  若运行环境的 SQLite 不带 trigram（< 3.34），自动降级为 ``unicode61``；
  查询侧由 :mod:`src.database.repo` 统一处理 1~2 字的短词（走 LIKE 兜底）。
* **字段与业务一一对应**：``selects_records`` 的列就是需求里那 7 个字段
  （姓名 / 届别 / 学院 / 专业 / 去向单位 / 城市 / 岗位）+ 省份、学历等辅助列。
"""
from __future__ import annotations

import sqlite3
from typing import List, Sequence, Tuple

#: 结构版本号，写入 ``meta`` 表，便于后续迁移判断
SCHEMA_VERSION = "2"

#: ``selects_records`` 的派生列：完全可由原始列重算，只服务于检索/筛选。
#: ``(列名, SQL 类型)``；由 :func:`ensure_columns` 负责老库增量迁移。
DERIVED_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("name_pinyin", "TEXT"),        # 姓名全拼（zhangsan），支撑拼音检索
    ("name_initials", "TEXT"),      # 姓名声母缩写（zs）
    ("position_category", "TEXT"),  # 岗位类别（技术研发类 / 教育类 / …）
    ("degree_level", "TEXT"),       # 学历层次（本科 / 硕士 / 博士）
)

#: 派生列上的索引，``(索引名, 列名)``。
#: 注意：这些索引不能写在 :data:`DDL_STATEMENTS` 里，否则老库（还没补列）
#: 会在 ``CREATE INDEX`` 时直接报 ``no such column``；统一由 :func:`ensure_columns` 负责。
DERIVED_INDEXES: Tuple[Tuple[str, str], ...] = (
    ("idx_rec_pinyin", "name_pinyin"),
    ("idx_rec_initials", "name_initials"),
    ("idx_rec_poscat", "position_category"),
    ("idx_rec_degree", "degree_level"),
)

#: FTS5 索引中包含的列（顺序即建表顺序）
FTS_COLUMNS: Tuple[str, ...] = (
    "name",
    "cohort",
    "college",
    "major",
    "destination_org",
    "city",
    "province",
    "position",
    "degree",
    "ocr_text",
    "notice_title",
)

#: ``selects_records`` 的可检索业务列（用于 LIKE 兜底）
SEARCHABLE_COLUMNS: Tuple[str, ...] = (
    "name",
    "cohort",
    "college",
    "major",
    "destination_org",
    "city",
    "province",
    "position",
    "degree",
    "raw_text",
)

# --------------------------------------------------------------------------- #
# DDL
# --------------------------------------------------------------------------- #
DDL_STATEMENTS: Tuple[str, ...] = (
    # ---- 元信息 ----
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """,
    # ---- 图片 / 通知公告 ----
    """
    CREATE TABLE IF NOT EXISTS images (
        image           TEXT PRIMARY KEY,   -- 图片文件名（与 data/raw/images 下一致）
        stem            TEXT UNIQUE,        -- 去扩展名，用于关联 OCR 中间产物
        notice_id       TEXT,               -- 门户通知 ID
        notice_title    TEXT,               -- 通知标题（含日期与活动名）
        organization    TEXT,               -- 发布单位
        release_time    TEXT,               -- 发布时间
        notice_type     TEXT,               -- 通知类型
        image_url       TEXT,               -- 原图地址
        alt             TEXT,               -- 图片原始文件名/描述
        sha256          TEXT,               -- 内容指纹，用于去重
        size_bytes      INTEGER,
        local_path      TEXT,
        ocr_engine      TEXT,
        ocr_line_count  INTEGER,
        ocr_mean_score  REAL,
        ocr_elapsed     REAL,
        ocr_error       TEXT,
        created_at      TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_images_notice   ON images(notice_id);",
    "CREATE INDEX IF NOT EXISTS idx_images_sha256   ON images(sha256);",
    "CREATE INDEX IF NOT EXISTS idx_images_release  ON images(release_time);",
    # ---- 选调生去向记录（核心表）----
    """
    CREATE TABLE IF NOT EXISTS selects_records (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        image           TEXT NOT NULL REFERENCES images(image) ON DELETE CASCADE,
        block_index     INTEGER NOT NULL DEFAULT 0,  -- 同图内第几位选调生
        name            TEXT,      -- 选调生姓名
        cohort          TEXT,      -- 届别，如 "2024届" / "2021级"
        cohort_year     INTEGER,   -- 届别年份（整数，便于排序/筛选）
        college         TEXT,      -- 所属学院
        major           TEXT,      -- 专业
        destination_org TEXT,      -- 选调去的单位
        city            TEXT,      -- 城市
        province        TEXT,      -- 省份（辅助）
        position        TEXT,      -- 岗位
        degree          TEXT,      -- 学历（辅助）
        confidence      REAL,      -- 抽取置信度 0~1
        field_count     INTEGER,   -- 命中的核心字段数 0~7
        evidence        TEXT,      -- 抽取依据（原文片段）
        raw_text        TEXT,      -- 该记录所在文本区块
        -- 以下为派生列：可由上面的原始列重算，见 src/nlp/derive.py
        name_pinyin        TEXT,   -- 姓名全拼（zhangsan）
        name_initials      TEXT,   -- 姓名声母缩写（zs）
        position_category  TEXT,   -- 岗位类别（技术研发类 / 教育类 / …）
        degree_level       TEXT,   -- 学历层次（本科 / 硕士 / 博士）
        created_at      TEXT DEFAULT (datetime('now', 'localtime')),
        UNIQUE (image, block_index)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_rec_name    ON selects_records(name);",
    "CREATE INDEX IF NOT EXISTS idx_rec_cohort  ON selects_records(cohort_year);",
    "CREATE INDEX IF NOT EXISTS idx_rec_college ON selects_records(college);",
    "CREATE INDEX IF NOT EXISTS idx_rec_city    ON selects_records(city);",
    "CREATE INDEX IF NOT EXISTS idx_rec_prov    ON selects_records(province);",
    "CREATE INDEX IF NOT EXISTS idx_rec_image   ON selects_records(image);",
)


def _fts_statement(tokenizer: str) -> str:
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS selects_fts USING fts5("
        + ", ".join(FTS_COLUMNS)
        + f", tokenize='{tokenizer}');"
    )


def detect_tokenizer(conn: sqlite3.Connection) -> str:
    """探测本机 SQLite 是否支持 trigram 分词器，返回可用的分词器名。"""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x, tokenize='trigram');")
        conn.execute("DROP TABLE IF EXISTS _fts_probe;")
        return "trigram"
    except sqlite3.OperationalError:
        return "unicode61 remove_diacritics 2"


def fts_tokenizer(conn: sqlite3.Connection) -> str:
    """读取（必要时探测并记录）FTS 分词器。"""
    row = conn.execute("SELECT value FROM meta WHERE key = 'fts_tokenizer'").fetchone()
    if row is not None:
        return str(row[0])
    tokenizer = detect_tokenizer(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('fts_tokenizer', ?)",
        (tokenizer,),
    )
    return tokenizer


def ensure_columns(conn: sqlite3.Connection) -> List[str]:
    """老库增量迁移：为 ``selects_records`` 补齐缺失的派生列。

    SQLite 的 ``ALTER TABLE ADD COLUMN`` 是廉价操作，因此这里对每个缺失列
    直接补一列，无需整表重建。返回本次新增的列名列表。

    顺带负责派生列索引的创建（必须在补列之后，否则会报 ``no such column``）。
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'selects_records'"
    ).fetchone():
        return []
    existing = {row[1] for row in conn.execute("PRAGMA table_info(selects_records)")}
    added: List[str] = []
    for column, decl in DERIVED_COLUMNS:
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE selects_records ADD COLUMN {column} {decl};")
        existing.add(column)
        added.append(column)
    for index, column in DERIVED_INDEXES:
        if column in existing:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index} ON selects_records({column});"
            )
    conn.commit()
    return added


def create_schema(conn: sqlite3.Connection) -> str:
    """建表（幂等），返回实际使用的 FTS 分词器。"""
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    ensure_columns(conn)
    tokenizer = fts_tokenizer(conn)
    conn.execute(_fts_statement(tokenizer))
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return tokenizer


def drop_schema(conn: sqlite3.Connection) -> None:
    """删除所有对象（仅用于全量重建）。"""
    for name in ("selects_fts", "selects_records", "images", "meta"):
        conn.execute(f"DROP TABLE IF EXISTS {name};")
    conn.commit()


def table_names(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
    ).fetchall()
    return [str(r[0]) for r in rows]


__all__ = [
    "DDL_STATEMENTS",
    "DERIVED_COLUMNS",
    "DERIVED_INDEXES",
    "FTS_COLUMNS",
    "SCHEMA_VERSION",
    "SEARCHABLE_COLUMNS",
    "create_schema",
    "detect_tokenizer",
    "drop_schema",
    "ensure_columns",
    "fts_tokenizer",
    "table_names",
]
