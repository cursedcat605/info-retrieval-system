"""数据访问层：SQLite + FTS5。

典型用法::

    from src.database import connect, search

    conn = connect()                     # 自动建表
    result = search(conn, "湖南 基层", limit=10)
    print(result["total"], result["items"][0]["name"])
"""
from __future__ import annotations

from .repo import (
    DEFAULT_SORT,
    FACET_DIMENSIONS,
    FILTER_COLUMNS,
    IMAGE_COLUMNS,
    RECORD_COLUMNS,
    SORT_ORDERS,
    connect,
    default_db_path,
    facet_counts,
    facets,
    get_record,
    init_db,
    match_ids,
    rebuild_fts,
    replace_records,
    search,
    source_text,
    stats,
    stats_overview,
    upsert_image,
)
from .schema import (
    DERIVED_COLUMNS,
    DERIVED_INDEXES,
    FTS_COLUMNS,
    SCHEMA_VERSION,
    SEARCHABLE_COLUMNS,
    create_schema,
    drop_schema,
    ensure_columns,
    fts_tokenizer,
    table_names,
)

__all__ = [
    "DEFAULT_SORT",
    "DERIVED_COLUMNS",
    "DERIVED_INDEXES",
    "FACET_DIMENSIONS",
    "FILTER_COLUMNS",
    "FTS_COLUMNS",
    "IMAGE_COLUMNS",
    "RECORD_COLUMNS",
    "SCHEMA_VERSION",
    "SEARCHABLE_COLUMNS",
    "SORT_ORDERS",
    "connect",
    "create_schema",
    "default_db_path",
    "drop_schema",
    "ensure_columns",
    "facet_counts",
    "facets",
    "fts_tokenizer",
    "get_record",
    "init_db",
    "match_ids",
    "rebuild_fts",
    "replace_records",
    "search",
    "source_text",
    "stats",
    "stats_overview",
    "table_names",
    "upsert_image",
]

