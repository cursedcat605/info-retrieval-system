"""SQLite 数据访问层：建库、写入、检索。

对外暴露的最小接口：

* :func:`init_db` / :func:`connect` —— 拿到可用连接（自动建表）
* :func:`upsert_image` / :func:`replace_records` / :func:`rebuild_fts` —— 写入
* :func:`search` —— 检索（FTS5 相关性排序 + LIKE 兜底）
* :func:`facets` / :func:`stats` —— 给前端做筛选面板与统计
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..utils.config import get, resolve_path
from ..utils.logger import get_logger
from ..utils.paths import ensure_dir, project_root
from ..utils.text import is_latin, normalize_latin
from .schema import SEARCHABLE_COLUMNS, create_schema, drop_schema, ensure_columns, fts_tokenizer

logger = get_logger("database")

#: 记录表的业务列（写入时用）
RECORD_COLUMNS: Sequence[str] = (
    "image",
    "block_index",
    "name",
    "cohort",
    "cohort_year",
    "college",
    "major",
    "destination_org",
    "city",
    "province",
    "position",
    "degree",
    "confidence",
    "field_count",
    "evidence",
    "raw_text",
)

#: images 表列
IMAGE_COLUMNS: Sequence[str] = (
    "image",
    "stem",
    "notice_id",
    "notice_title",
    "organization",
    "release_time",
    "notice_type",
    "image_url",
    "alt",
    "sha256",
    "size_bytes",
    "local_path",
    "ocr_engine",
    "ocr_line_count",
    "ocr_mean_score",
    "ocr_elapsed",
    "ocr_error",
)

#: FTS 索引列
_FTS_COLUMNS: Sequence[str] = (
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

_LIKE_ESCAPE = str.maketrans({"\\": r"\\", "%": r"\%", "_": r"\_"})


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
def default_db_path() -> Path:
    """数据库文件路径（config 优先，缺省 ``data/db/selects.sqlite``）。"""
    configured = get(["database", "sqlite_path"], None)
    if configured:
        return resolve_path(str(configured))
    return project_root() / "data" / "db" / "selects.sqlite"


def connect(
    path: Optional[Path] = None,
    *,
    create: bool = True,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """打开（并自动建表）数据库连接。

    ``check_same_thread=False`` 专供 Web 层：FastAPI 的**同步生成器依赖**由
    ``contextmanager_in_threadpool`` 调度，同一次请求内「建连接 / 用连接 / 关连接」
    会落在 anyio 线程池的**不同工作线程**上（串行、不并发）。SQLite 默认禁止
    跨线程使用连接，并发请求下会抛 ``ProgrammingError``。由于每个请求独占一个
    连接、任意时刻只有一个线程在用它，放开该检查是安全的。
    脚本与测试保持默认 ``True``，以便尽早暴露误用。
    """
    db_path = Path(path) if path else default_db_path()
    ensure_dir(db_path.parent)
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    if create:
        create_schema(conn)
    else:
        ensure_columns(conn)
    return conn


def init_db(path: Optional[Path] = None, *, reset: bool = False) -> sqlite3.Connection:
    """初始化数据库；``reset=True`` 时先清空所有表再重建。"""
    db_path = Path(path) if path else default_db_path()
    conn = connect(db_path, create=False)
    if reset:
        drop_schema(conn)
        logger.info("已清空数据库：%s", db_path)
    tokenizer = create_schema(conn)
    logger.info("数据库就绪：%s（FTS 分词器 %s）", db_path, tokenizer)
    return conn


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def upsert_image(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    """插入或更新一条图片记录（按 ``image`` 主键 UPSERT）。"""
    data = {col: row.get(col) for col in IMAGE_COLUMNS}
    if not data.get("image"):
        raise ValueError("images 记录缺少 image 字段")
    columns = ", ".join(IMAGE_COLUMNS)
    placeholders = ", ".join("?" for _ in IMAGE_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in IMAGE_COLUMNS if c != "image")
    conn.execute(
        f"INSERT INTO images ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(image) DO UPDATE SET {updates}",
        [data[col] for col in IMAGE_COLUMNS],
    )


def replace_records(conn: sqlite3.Connection, image: str, records: Iterable[Mapping[str, Any]]) -> int:
    """覆盖某张图片的全部去向记录，返回写入条数（幂等）。"""
    conn.execute("DELETE FROM selects_records WHERE image = ?", (image,))
    columns = ", ".join(RECORD_COLUMNS)
    placeholders = ", ".join("?" for _ in RECORD_COLUMNS)
    count = 0
    for idx, record in enumerate(records):
        data = dict(record)
        data["image"] = image
        data.setdefault("block_index", idx)
        conn.execute(
            f"INSERT INTO selects_records ({columns}) VALUES ({placeholders})",
            [data.get(col) for col in RECORD_COLUMNS],
        )
        count += 1
    return count


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """按 ``selects_records`` 全量重建 FTS 索引，返回索引条数。"""
    conn.execute("DELETE FROM selects_fts;")
    rows = conn.execute(
        """
        SELECT r.id,
               r.name, r.cohort, r.college, r.major, r.destination_org,
               r.city, r.province, r.position, r.degree,
               r.raw_text AS ocr_text,
               COALESCE(i.notice_title, '') AS notice_title
        FROM selects_records r
        LEFT JOIN images i ON i.image = r.image
        """
    ).fetchall()
    columns = ", ".join(_FTS_COLUMNS)
    placeholders = ", ".join("?" for _ in _FTS_COLUMNS)
    conn.executemany(
        f"INSERT INTO selects_fts (rowid, {columns}) VALUES (?, {placeholders})",
        [[r["id"]] + [r[col] or "" for col in _FTS_COLUMNS] for r in rows],
    )
    conn.commit()
    logger.info("FTS 索引已重建：%d 条", len(rows))
    return len(rows)


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def _like_pattern(keyword: str) -> str:
    return f"%{keyword.translate(_LIKE_ESCAPE)}%"


#: 筛选项 → ``selects_records`` 列名（分组内 OR，分组间 AND）
FILTER_COLUMNS: Dict[str, str] = {
    "province": "r.province",
    "city": "r.city",
    "college": "r.college",
    "major": "r.major",
    "cohort_year": "r.cohort_year",
    "degree_level": "r.degree_level",
    "position_category": "r.position_category",
}

#: 筛选面板的维度顺序与中文名（前端按此顺序渲染分组）
FACET_DIMENSIONS: Tuple[Tuple[str, str], ...] = (
    ("province", "省份"),
    ("position_category", "岗位类别"),
    ("degree_level", "学历"),
    ("cohort_year", "届别"),
    ("college", "学院"),
    ("major", "专业"),
    ("city", "城市"),
)

#: 排序方式 → ORDER BY 片段（``relevance`` 为 None，运行时按检索模式决定）
SORT_ORDERS: Dict[str, Optional[str]] = {
    "relevance": None,
    "cohort_desc": "r.cohort_year DESC, r.confidence DESC, r.id",
    "cohort_asc": "r.cohort_year ASC, r.confidence DESC, r.id",
    "name_pinyin": "r.name_pinyin ASC, r.name ASC, r.id",
    "confidence_desc": "r.confidence DESC, r.cohort_year DESC, r.id",
}

#: 默认排序（需求 11.1：默认按届别从新到旧）
DEFAULT_SORT = "cohort_desc"

_RELEVANCE_FTS = "bm25(selects_fts), r.confidence DESC, r.id"
_RELEVANCE_LIKE = "r.confidence DESC, r.cohort_year DESC, r.id"


def _dimension_clause(dimension: str, values: Any) -> Tuple[Optional[str], List[Any]]:
    """把单个维度的多选值转成 ``col IN (?, ?, …)``（同维度 OR）。"""
    column = FILTER_COLUMNS.get(dimension)
    if not column or values is None:
        return None, []
    if isinstance(values, (str, int)):
        values = [values]
    cleaned: List[Any] = []
    for value in values:
        if dimension == "cohort_year":
            try:
                cleaned.append(int(value))
            except (TypeError, ValueError):
                continue
        else:
            text = str(value or "").strip()
            if text:
                cleaned.append(text)
    if not cleaned:
        return None, []
    placeholders = ", ".join("?" for _ in cleaned)
    return f"{column} IN ({placeholders})", cleaned


def _normalize_filters(filters: Optional[Mapping[str, Any]]) -> Dict[str, List[Any]]:
    """去掉空值、归一化为 ``{维度: [值, …]}``。"""
    result: Dict[str, List[Any]] = {}
    for dimension, values in (filters or {}).items():
        if dimension not in FILTER_COLUMNS or values is None:
            continue
        if isinstance(values, (str, int)):
            values = [values]
        kept = [v for v in values if str(v or "").strip()]
        if kept:
            result[dimension] = kept
    return result


def _keyword_clause(keyword: str, tokenizer: str) -> Tuple[Optional[str], List[Any], bool]:
    """关键词 → ``(WHERE 片段, 参数, 是否走 FTS)``。

    三条路径：

    * 中文且 ≥ 3 字且环境支持 trigram → FTS5 ``MATCH``（bm25 相关度排序）；
    * 拉丁输入（``zhangsan``） → 额外叠加姓名拼音/声母条件；
    * 其余（1~2 字短中文词 / unicode61 环境）→ 关键列 ``LIKE`` 兜底。
    """
    kw = (keyword or "").strip()
    if not kw:
        return None, [], False

    latin = is_latin(kw)
    if len(kw) >= 3 and not latin and "trigram" in (tokenizer or ""):
        return "selects_fts MATCH ?", ['"' + kw.replace('"', '""') + '"'], True

    patterns = [f"r.{column} LIKE ? ESCAPE '\\'" for column in SEARCHABLE_COLUMNS]
    params: List[Any] = [_like_pattern(kw)] * len(SEARCHABLE_COLUMNS)
    if latin:
        normalized = normalize_latin(kw)
        if normalized:
            if len(normalized) <= 2:
                # 1~2 个字母：只认声母前缀，避免过于宽泛的误召回
                patterns.append("r.name_initials LIKE ? ESCAPE '\\'")
                params.append(f"{normalized}%")
            else:
                patterns.append(
                    "(r.name_pinyin LIKE ? ESCAPE '\\' OR r.name_initials = ?)"
                )
                params.extend([_like_pattern(normalized), normalized])
    return "(" + " OR ".join(patterns) + ")", params, False


def _build_from_where(
    keyword: str = "",
    *,
    filters: Optional[Mapping[str, Any]] = None,
    name: Optional[str] = None,
    min_confidence: Optional[float] = None,
    record_ids: Optional[Sequence[int]] = None,
    exclude_dimension: Optional[str] = None,
    tokenizer: str = "",
) -> Tuple[str, str, List[Any], bool]:
    """组装 ``(FROM 片段, WHERE 片段, 参数, 是否走 FTS)``。"""
    clauses: List[str] = []
    params: List[Any] = []

    keyword_sql, keyword_params, use_fts = _keyword_clause(keyword, tokenizer)
    if keyword_sql:
        clauses.append(keyword_sql)
        params.extend(keyword_params)

    for dimension, values in _normalize_filters(filters).items():
        if dimension == exclude_dimension:
            continue
        clause, dim_params = _dimension_clause(dimension, values)
        if clause:
            clauses.append(clause)
            params.extend(dim_params)

    if name:
        clauses.append("r.name LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(name))
    if min_confidence is not None:
        clauses.append("r.confidence >= ?")
        params.append(min_confidence)
    if record_ids is not None:
        ids = []
        for value in record_ids:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        if not ids:
            clauses.append("0 = 1")
        else:
            # 直接内联整数（已强制转 int，无注入风险），避免 SQLite 变量数上限
            clauses.append("r.id IN (" + ", ".join(str(i) for i in ids) + ")")

    from_sql = _FROM_RECORDS_FTS if use_fts else _FROM_RECORDS
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return from_sql, where_sql, params, use_fts


def _order_sql(sort: str, use_fts: bool) -> str:
    """把排序标识符翻译成 ``ORDER BY`` 片段。"""
    order = SORT_ORDERS.get(sort)
    if order:
        return order
    if sort not in SORT_ORDERS:
        logger.debug("未知排序方式 %s，回退到相关度排序", sort)
    return _RELEVANCE_FTS if use_fts else _RELEVANCE_LIKE


_SELECT_RECORD_COLUMNS = """
    r.id, r.image, r.block_index,
    r.name, r.cohort, r.cohort_year, r.college, r.major,
    r.destination_org, r.city, r.province, r.position, r.degree,
    r.position_category, r.degree_level, r.name_pinyin, r.name_initials,
    r.confidence, r.field_count, r.evidence, r.raw_text,
    i.notice_id, i.notice_title, i.organization, i.release_time, i.image_url
"""

#: 仅详情页需要的附加列：原图定位信息与 OCR 质量指标（列表页不返回，减小体积）
#: 注意以逗号开头，追加在 ``_SELECT_RECORD_COLUMNS`` 之后使用。
_DETAIL_EXTRA_COLUMNS = """
    , i.stem, i.local_path, i.notice_type, i.alt, i.size_bytes,
    i.ocr_engine, i.ocr_line_count, i.ocr_mean_score, i.ocr_elapsed, i.ocr_error
"""

_SELECT_DETAIL_COLUMNS = _SELECT_RECORD_COLUMNS.rstrip() + _DETAIL_EXTRA_COLUMNS

_FROM_RECORDS = """
    FROM selects_records r
    LEFT JOIN images i ON i.image = r.image
"""

#: 注意：SQLite 的 FTS5 ``MATCH`` 左操作数必须是**未取别名的表名**，
#: 写成 ``f MATCH ?`` 会报 "no such column: f"。因此这里保持原名。
_FROM_RECORDS_FTS = """
    FROM selects_records r
    JOIN selects_fts ON selects_fts.rowid = r.id
    LEFT JOIN images i ON i.image = r.image
"""


def search(
    conn: sqlite3.Connection,
    query: str = "",
    *,
    filters: Optional[Mapping[str, Any]] = None,
    province: Optional[str] = None,
    city: Optional[str] = None,
    college: Optional[str] = None,
    cohort_year: Optional[int] = None,
    name: Optional[str] = None,
    min_confidence: Optional[float] = None,
    record_ids: Optional[Sequence[int]] = None,
    sort: str = "relevance",
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """检索去向记录（单关键词 + 多条件筛选 + 排序 + 分页）。

    关键词匹配策略：

    1. 中文且 ≥ 3 字且分词器为 trigram → ``selects_fts MATCH``，按 bm25 排序；
    2. 拉丁输入（``zhangsan``）→ 姓名拼音/声母匹配，并叠加普通列 ``LIKE``；
    3. 其余（空词 / 1~2 字短词）→ 关键列 ``LIKE`` 兜底。

    :param filters: 多值筛选，如 ``{"province": ["湖北省", "江苏省"],
        "degree_level": ["本科"]}``。**同维度内 OR，不同维度间 AND**。
    :param record_ids: 限定候选记录集（供多关键词 AND/OR 求交后再排序分页）。
    :param sort: ``relevance`` / ``cohort_desc`` / ``cohort_asc`` /
        ``name_pinyin`` / ``confidence_desc``。
    :return: ``{"total", "items", "mode", "sort"}``。
    """
    # 兼容早期单值调用签名
    legacy = {
        "province": province,
        "city": city,
        "college": college,
        "cohort_year": cohort_year,
    }
    merged: Dict[str, Any] = {k: v for k, v in legacy.items() if v is not None}
    for dimension, values in (filters or {}).items():
        existing = merged.get(dimension)
        if existing is None:
            merged[dimension] = values
        else:
            merged[dimension] = list(existing) + list(values)

    tokenizer = fts_tokenizer(conn)
    from_sql, where_sql, params, use_fts = _build_from_where(
        query,
        filters=merged,
        name=name,
        min_confidence=min_confidence,
        record_ids=record_ids,
        tokenizer=tokenizer,
    )
    total = conn.execute(
        f"SELECT COUNT(*) {from_sql}{where_sql}", params
    ).fetchone()[0]

    mode = "fts" if use_fts else ("like" if (query or "").strip() else "all")
    if is_latin(query or ""):
        mode = "pinyin"

    items: List[Dict[str, Any]] = []
    if total:
        sql = (
            f"SELECT {_SELECT_RECORD_COLUMNS} {from_sql}{where_sql}"
            f" ORDER BY {_order_sql(sort, use_fts)} LIMIT ? OFFSET ?"
        )
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        items = [dict(row) for row in rows]
    return {"total": int(total), "items": items, "mode": mode, "sort": sort}


def match_ids(
    conn: sqlite3.Connection,
    query: str,
    *,
    filters: Optional[Mapping[str, Any]] = None,
    limit: Optional[int] = None,
) -> List[int]:
    """返回命中的记录 id（按相关度排序），供多关键词 AND/OR 运算使用。"""
    result = search(
        conn, query, filters=filters, sort="relevance",
        limit=limit if limit is not None else 1000, offset=0,
    )
    return [int(row["id"]) for row in result["items"]]


def get_record(conn: sqlite3.Connection, record_id: int) -> Optional[Dict[str, Any]]:
    """读取单条记录的完整信息（含原图定位与 OCR 质量指标，供详情页使用）。"""
    row = conn.execute(
        f"SELECT {_SELECT_DETAIL_COLUMNS} {_FROM_RECORDS} WHERE r.id = ?", (record_id,)
    ).fetchone()
    return dict(row) if row else None


def source_text(conn: sqlite3.Connection, record_id: int) -> Optional[str]:
    """返回记录所属原图的 OCR 全文（读取 ``ocr.output_dir`` 下的同名 ``.txt``）。

    详情页需要展示「原文」，而表里只存了命中块（``raw_text``）；整图文本落盘在
    ``data/interim/ocr_text/<stem>.txt``，这里按需读取，读不到则返回 ``None``。
    """
    row = conn.execute(
        "SELECT i.stem FROM selects_records r LEFT JOIN images i ON i.image = r.image "
        "WHERE r.id = ?",
        (record_id,),
    ).fetchone()
    if not row or not row["stem"]:
        return None
    ocr_dir = get(["ocr", "output_dir"], "data/interim/ocr_text") or "data/interim/ocr_text"
    path = resolve_path(str(ocr_dir)) / f"{row['stem']}.txt"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - 文件被占用等异常情况
        logger.warning("读取原文失败：%s", path)
        return None


def facets(conn: sqlite3.Connection, limit: int = 100) -> Dict[str, List[Dict[str, Any]]]:
    """返回各字段的可选值与计数（无关键词、无筛选时的全局分面）。

    用于搜索框自动补全等场景；带筛选条件的分面计数见 :func:`facet_counts`。
    """
    result = facet_counts(conn, "", limit=limit)
    org_rows = conn.execute(
        "SELECT destination_org AS value, COUNT(*) AS count FROM selects_records "
        "WHERE COALESCE(destination_org, '') != '' "
        "GROUP BY destination_org ORDER BY count DESC, value LIMIT ?",
        (limit,),
    ).fetchall()
    result["destination_org"] = [
        {"value": r["value"], "count": int(r["count"])} for r in org_rows
    ]
    result["cohort"] = list(result.get("cohort_year", []))
    return result


def facet_counts(
    conn: sqlite3.Connection,
    query: str = "",
    *,
    filters: Optional[Mapping[str, Any]] = None,
    record_ids: Optional[Sequence[int]] = None,
    dimensions: Optional[Sequence[str]] = None,
    limit: int = 300,
) -> Dict[str, List[Dict[str, Any]]]:
    """带计数的分面（需求十：每个选项旁显示当前结果数量）。

    每个维度的计数都**排除该维度自身**的选择，只受其余维度与关键词约束：
    这样用户勾选“湖北”后，仍能看到“江苏 (86)”这类未选项的可选数量，
    否则未被选中的选项会全部变成 0，无法判断是否值得加入。

    :return: ``{"province": [{"value": "湖北省", "count": 128}, …], …}``
    """
    tokenizer = fts_tokenizer(conn)
    names = [d for d, _ in FACET_DIMENSIONS] if dimensions is None else list(dimensions)
    result: Dict[str, List[Dict[str, Any]]] = {}
    for dimension in names:
        column = FILTER_COLUMNS.get(dimension)
        if not column:
            continue
        from_sql, where_sql, params, _ = _build_from_where(
            query,
            filters=filters,
            record_ids=record_ids,
            exclude_dimension=dimension,
            tokenizer=tokenizer,
        )
        non_empty = f"COALESCE({column}, '') != ''"
        full_where = f"{where_sql} AND {non_empty}" if where_sql else f" WHERE {non_empty}"
        rows = conn.execute(
            f"SELECT {column} AS value, COUNT(*) AS count {from_sql}{full_where} "
            f"GROUP BY {column} ORDER BY count DESC, value LIMIT ?",
            params + [limit],
        ).fetchall()
        result[dimension] = [
            {"value": r["value"], "count": int(r["count"])} for r in rows
        ]
    return result


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """整体数据概览：图片数、记录数、各核心字段填充率。"""
    images = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    latest_release_time = conn.execute(
        "SELECT MAX(release_time) FROM images "
        "WHERE COALESCE(release_time, '') != ''"
    ).fetchone()[0]
    records = conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0]
    rows = conn.execute("SELECT * FROM selects_records").fetchall()
    fill: Dict[str, float] = {}
    for column in (
        "name", "cohort", "college", "major", "destination_org", "city", "position",
        "province", "degree", "position_category", "degree_level",
    ):
        filled = sum(1 for r in rows if r[column])
        fill[column] = round(filled / records, 3) if records else 0.0
    empty = conn.execute(
        "SELECT COUNT(*) FROM selects_records "
        "WHERE COALESCE(name,'') = '' AND COALESCE(college,'') = '' "
        "AND COALESCE(destination_org,'') = ''"
    ).fetchone()[0]
    return {
        "images": int(images),
        "records": int(records),
        "latest_release_time": latest_release_time,
        "empty_records": int(empty),
        "fill_rate": fill,
    }


def stats_overview(conn: sqlite3.Connection, top_n: int = 15) -> Dict[str, Any]:
    """统计分析页所需的聚合数据（需求十五）。

    返回 5 组分布 + 1 个概览，全部以 ``{"value", "count"}`` 列表给出，
    前端直接丢给 ECharts，无需再做二次汇总。
    """

    def group(column: str, *, limit: Optional[int] = None, ascending: bool = False):
        order = "value ASC" if ascending else "count DESC, value"
        sql = (
            f"SELECT {column} AS value, COUNT(*) AS count FROM selects_records "
            f"WHERE COALESCE({column}, '') != '' GROUP BY {column} ORDER BY {order}"
        )
        if limit is not None:
            return conn.execute(sql + " LIMIT ?", (limit,)).fetchall()
        return conn.execute(sql).fetchall()

    def to_list(rows) -> List[Dict[str, Any]]:
        return [{"value": r["value"], "count": int(r["count"])} for r in rows]

    overview = stats(conn)
    return {
        "summary": {
            "records": overview["records"],
            "images": overview["images"],
            "provinces": len(group("province")),
            "colleges": len(group("college")),
            "cities": len(group("city")),
            "cohorts": len(group("cohort_year")),
        },
        # 省份分布（横向柱状图）
        "province": to_list(group("province", limit=top_n)),
        # 届别趋势（折线图，按年份升序）
        "cohort": to_list(group("cohort_year", ascending=True)),
        # 岗位类别（饼图）
        "position_category": to_list(group("position_category")),
        # 学院分布（横向柱状图）
        "college": to_list(group("college", limit=top_n)),
        # 学历层次
        "degree_level": to_list(group("degree_level")),
        # 去向城市 TOP N
        "city": to_list(group("city", limit=top_n)),
        "fill_rate": overview["fill_rate"],
    }


__all__ = [
    "DEFAULT_SORT",
    "FACET_DIMENSIONS",
    "FILTER_COLUMNS",
    "IMAGE_COLUMNS",
    "RECORD_COLUMNS",
    "SORT_ORDERS",
    "connect",
    "default_db_path",
    "facet_counts",
    "facets",
    "get_record",
    "init_db",
    "match_ids",
    "rebuild_fts",
    "replace_records",
    "search",
    "source_text",
    "stats",
    "stats_overview",
    "upsert_image",
]
