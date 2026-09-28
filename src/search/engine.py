"""检索层：查询解析 → 多条件筛选 → 排序分页 → 结果高亮。

上层（FastAPI / CLI）只需调用 :func:`query`，不必关心：

* 搜索框里的多关键词如何切成 AND / OR 语义；
* FTS5 的短语语法、trigram 与 ``LIKE`` 兜底如何选择；
* 拼音输入（``zhangsan``）如何落到汉字（``张三``）；
* 分面计数为什么必须“排除自己”才能给出未选项的真实数量。

返回结构面向前端直接消费，字段含义见 :func:`query` 的文档字符串。
"""
from __future__ import annotations

import math
import re
import sqlite3
from typing import Any, Dict, List, Mapping, Optional

from ..database.repo import (
    DEFAULT_SORT,
    FACET_DIMENSIONS,
    FILTER_COLUMNS,
    SORT_ORDERS,
    facet_counts as _facet_counts,
    facets as _facets,
    get_record as _get_record,
    match_ids as _match_ids,
    search as _db_search,
    source_text as _source_text,
)
from ..nlp import pinyin as pinyin_util
from ..utils.config import get
from ..utils.logger import get_logger
from ..utils.text import shorten_region, unique_keep_order
from . import highlight as highlight_util

logger = get_logger("search")

#: 查询串分隔符（空格、逗号、顿号、加号、竖线等）
_SPLIT_RE = re.compile(r"[\s,，、;；+|/]+")

#: 默认每页条数 / 单页上限
DEFAULT_PAGE_SIZE = int(get(["search", "page_size"], 20) or 20)
MAX_PAGE_SIZE = int(get(["search", "max_page_size"], 100) or 100)

#: 筛选维度的中文名（来源：数据库层的分面定义，保证两处不会走偏）
DIMENSION_LABELS: Dict[str, str] = dict(FACET_DIMENSIONS)

#: 排序方式的中文名（需求 11 需要在前端提供切换）
SORT_LABELS: Dict[str, str] = {
    "relevance": "相关度",
    "cohort_desc": "届别（新→旧）",
    "cohort_asc": "届别（旧→新）",
    "name_pinyin": "姓名拼音",
    "confidence_desc": "置信度",
}

#: 多关键词逐词取候选时的上限（当前数据量下远超实际记录数）
_TERM_CANDIDATE_LIMIT = 2000


def parse_query(query: str) -> List[str]:
    """把查询串切成关键词列表（去空、去重、保序）。"""
    terms: List[str] = []
    for term in _SPLIT_RE.split((query or "").strip()):
        term = term.strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def parse_filters(params: Optional[Mapping[str, Any]]) -> Dict[str, List[str]]:
    """把请求参数里的筛选值归一化为 ``{维度: [值, …]}``。

    接受 ``{"province": "湖北省"}`` 与 ``{"province": ["湖北省", "江苏省"]}``
    两种写法，并丢弃未知维度与空值。
    """
    result: Dict[str, List[str]] = {}
    for dimension, value in (params or {}).items():
        if dimension not in FILTER_COLUMNS or value is None:
            continue
        values = [value] if isinstance(value, (str, int)) else list(value)
        kept = unique_keep_order(str(v) for v in values)
        if kept:
            result[dimension] = kept
    return result


def _display_value(dimension: str, value: Any) -> str:
    """筛选值的展示形式（省份去“省”、届别补“届”）。"""
    if dimension == "cohort_year":
        return f"{value}届"
    if dimension == "province":
        return shorten_region(str(value))
    return str(value)


def _filter_tags(filters: Mapping[str, List[str]]) -> List[Dict[str, str]]:
    """已选条件的标签列表（需求十：[湖北 ×] [本科 ×]）。"""
    tags: List[Dict[str, str]] = []
    for dimension, label in FACET_DIMENSIONS:
        for value in filters.get(dimension, []):
            tags.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "label": label,
                    "display": _display_value(dimension, value),
                }
            )
    return tags


def _mark_facets(
    facet_data: Mapping[str, List[Dict[str, Any]]],
    filters: Mapping[str, List[str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """给分面选项补上展示名与 ``selected`` 标记，并固定维度顺序。"""
    result: Dict[str, List[Dict[str, Any]]] = {}
    for dimension, _label in FACET_DIMENSIONS:
        chosen = {str(v) for v in filters.get(dimension, [])}
        result[dimension] = [
            {
                "value": option["value"],
                "count": option["count"],
                "label": _display_value(dimension, option["value"]),
                "selected": str(option["value"]) in chosen,
            }
            for option in facet_data.get(dimension, [])
        ]
    return result


def _sort_options() -> List[Dict[str, str]]:
    return [
        {"value": key, "label": SORT_LABELS.get(key, key)}
        for key in SORT_ORDERS
    ]


def query(
    conn: sqlite3.Connection,
    query: str = "",
    *,
    filters: Optional[Mapping[str, Any]] = None,
    sort: str = "",
    page: int = 1,
    page_size: Optional[int] = None,
    with_facets: bool = True,
    with_highlight: bool = True,
) -> Dict[str, Any]:
    """执行一次带筛选/排序/分页/高亮的检索。

    :param query: 用户输入，可含空格分隔的多个关键词。
    :param filters: 多选筛选，同维度 OR、跨维度 AND。
    :param sort: 见 :data:`SORT_LABELS`；留空时“有查询词→相关度，否则→默认届别倒序”。
    :param page: 页码，从 1 开始。
    :return: ``{"query", "terms", "mode", "total", "page", "pages", "items",
        "facets", "filter_tags", "dimensions", "sort_options", "groups"}``
    """
    terms = parse_query(query)
    active = parse_filters(filters)

    try:
        page_number = max(1, int(page or 1))
    except (TypeError, ValueError):
        page_number = 1
    try:
        size = int(page_size or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    size = max(1, min(size, MAX_PAGE_SIZE))
    offset = (page_number - 1) * size

    resolved_sort = (sort or "").strip() or ("relevance" if terms else DEFAULT_SORT)

    keyword = terms[0] if len(terms) == 1 else ""
    groups: List[Dict[str, Any]] = []
    candidate_ids: Optional[List[int]] = None
    mode = "all"

    if len(terms) <= 1:
        result = _db_search(
            conn, keyword, filters=active, sort=resolved_sort,
            limit=size, offset=offset,
        )
        total = int(result["total"])
        items = result["items"]
        mode = result["mode"]
    else:
        # 多关键词：逐词各取一批候选，再按“全命中优先”组合
        per_term_ids: List[List[int]] = []
        for term in terms:
            ids = _match_ids(conn, term, filters=active, limit=_TERM_CANDIDATE_LIMIT)
            per_term_ids.append(ids)
            groups.append({"term": term, "total": len(ids)})

        sets = [set(ids) for ids in per_term_ids]
        common = set.intersection(*sets) if sets else set()
        if common:
            mode = "and"
            candidate_ids = sorted(common)
            total = len(candidate_ids)
            result = _db_search(
                conn, "", filters=active, record_ids=candidate_ids,
                sort=resolved_sort, limit=size, offset=offset,
            )
            items = result["items"]
        else:
            mode = "or"
            hits: Dict[int, int] = {}
            for ids in per_term_ids:
                for rid in ids:
                    hits[rid] = hits.get(rid, 0) + 1
            candidate_ids = sorted(hits, key=lambda rid: (-hits[rid], rid))
            total = len(candidate_ids)
            if resolved_sort == "relevance":
                page_ids = candidate_ids[offset: offset + size]
                items = []
                if page_ids:
                    found = _db_search(
                        conn, "", filters=active, record_ids=page_ids,
                        sort="confidence_desc", limit=len(page_ids), offset=0,
                    )
                    by_id = {row["id"]: row for row in found["items"]}
                    items = [by_id[rid] for rid in page_ids if rid in by_id]
            else:
                result = _db_search(
                    conn, "", filters=active, record_ids=candidate_ids,
                    sort=resolved_sort, limit=size, offset=offset,
                )
                items = result["items"]

    facet_data: Dict[str, List[Dict[str, Any]]] = {}
    if with_facets:
        if len(terms) <= 1:
            facet_data = _facet_counts(conn, keyword, filters=active)
        else:
            facet_data = _facet_counts(
                conn, "", filters=active, record_ids=candidate_ids
            )

    if with_highlight:
        for item in items:
            item["highlight"] = highlight_util.build(item, terms)
    else:
        for item in items:
            item["highlight"] = {
                "fields": {field: highlight_util.mark(item.get(field), None)[0]
                           for field, _ in highlight_util.FIELD_LABELS},
                "matched_fields": [],
                "snippet": None,
                "matched_terms": [],
            }

    pages = math.ceil(total / size) if total else 0
    return {
        "query": query,
        "terms": terms,
        "mode": mode,
        "total": total,
        "page": page_number,
        "page_size": size,
        "pages": pages,
        "has_prev": page_number > 1,
        "has_next": page_number < pages,
        "sort": resolved_sort,
        "sort_options": _sort_options(),
        "filters": active,
        "filter_tags": _filter_tags(active),
        "facets": _mark_facets(facet_data, active),
        "dimensions": [{"key": key, "label": label} for key, label in FACET_DIMENSIONS],
        "groups": groups,
        "pinyin": pinyin_util.available(),
        "items": items,
    }


def get_detail(
    conn: sqlite3.Connection, record_id: int
) -> Optional[Dict[str, Any]]:
    """详情页数据：完整字段 + 来源信息 + 原文证据（需求十四）。

    返回除记录与图片元数据外，还包含：

    * ``highlight``      —— 与列表页同构的转义后字段（详情页无关键词，仅做转义）
    * ``field_labels``   —— 字段顺序与中文名，供前端渲染三栏
    * ``source_text``    —— 原图 OCR 全文（读不到时为 ``None``，前端回退到 ``raw_text``）
    """
    record = _get_record(conn, record_id)
    if not record:
        return None
    record["highlight"] = highlight_util.build(record, ())
    record["field_labels"] = [
        {"key": key, "label": label} for key, label in highlight_util.FIELD_LABELS
    ]
    record["source_text"] = _source_text(conn, record_id)
    return record


def suggestions(conn: sqlite3.Connection) -> Dict[str, Any]:
    """给搜索框的自动补全用：返回已有省份/城市/学院/专业候选词。"""
    data = _facets(conn)
    return {
        "provinces": [f["value"] for f in data.get("province", [])],
        "cities": [f["value"] for f in data.get("city", [])],
        "colleges": [f["value"] for f in data.get("college", [])],
        "majors": [f["value"] for f in data.get("major", [])],
        "cohorts": [f["value"] for f in data.get("cohort_year", [])],
        "pinyin": pinyin_util.available(),
    }


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DIMENSION_LABELS",
    "MAX_PAGE_SIZE",
    "SORT_LABELS",
    "get_detail",
    "parse_filters",
    "parse_query",
    "query",
    "suggestions",
]
