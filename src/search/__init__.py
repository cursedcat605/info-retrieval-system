"""检索模块：查询解析、多条件筛选、排序分页、结果高亮。

后端调用入口::

    from src.database import connect
    from src.search import query

    conn = connect()
    result = query(conn, "湖南 基层", filters={"city": ["株洲市"]}, sort="cohort_desc")
    for item in result["items"]:
        print(item["highlight"]["fields"]["name"])
"""
from __future__ import annotations

from . import fuzzy
from . import highlight
from .engine import (
    DEFAULT_PAGE_SIZE,
    DIMENSION_LABELS,
    MAX_PAGE_SIZE,
    SORT_LABELS,
    get_detail,
    parse_filters,
    parse_query,
    query,
    suggestions,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DIMENSION_LABELS",
    "MAX_PAGE_SIZE",
    "SORT_LABELS",
    "fuzzy",
    "get_detail",
    "highlight",
    "parse_filters",
    "parse_query",
    "query",
    "suggestions",
]

