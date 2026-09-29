"""FastAPI 应用：选调生去向信息检索系统 HTTP 接口。

接口一览（全部只读）::

    GET /api/config                     前端启动所需的字典：维度中文名/排序项/分页上限
    GET /api/search                     检索（关键词 + 拼音 + 多条件筛选 + 排序 + 分页 + 高亮）
    GET /api/record/{record_id}         记录详情（三栏 + 原文全文 + 原文证据）
    GET /api/facets                     只取分面计数（前端局部刷新用）
    GET /api/stats                      统计分析（ECharts 数据源，需求十五）
    GET /api/suggest                    搜索框自动补全候选
    GET /api/health                     健康检查与数据体检
    GET /images/{filename}              原图（本地缓存）静态访问
    GET /                               前端页面（web/frontend）

多值参数约定：同一维度重复出现即视为「组内 OR」，例如
``?province=湖北省&province=湖南省&degree_level=本科``，
与需求十的「同维度内 OR，不同维度间 AND」一致。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.database.repo import (
    connect,
    default_db_path,
    facet_counts,
    stats,
    stats_overview,
)
from src.search import (
    DEFAULT_PAGE_SIZE,
    DIMENSION_LABELS,
    MAX_PAGE_SIZE,
    SORT_LABELS,
    fuzzy as fuzzy_terms,
    get_detail,
    query as search_query,
    suggestions,
)
from src.utils.config import get, resolve_path
from src.utils.logger import get_logger
from src.utils.paths import data_dir, project_root

logger = get_logger("web")

PROJECT_ROOT = project_root()
FRONTEND_DIR = PROJECT_ROOT / "web" / "frontend"
IMAGE_DIR = data_dir("raw", "images")

#: 门户「通知公告」列表页支持关键词定位，用它拼「查看原文」链接
_PORTAL_LIST_URL = (
    get(["web", "portal_list_url"], "")
    or "https://my.muc.edu.cn/page/11#/notice/noticeList?lo={keyword}"
)
#: 若门户有稳定的详情页地址模板（含 ``{notice_id}``），配置后优先使用
_PORTAL_DETAIL_URL = get(["web", "portal_detail_url"], "") or ""
_PORTAL_HOME = get(["web", "portal_home"], "https://my.muc.edu.cn/page/11")


# --------------------------------------------------------------------------- #
# 依赖
# --------------------------------------------------------------------------- #
def get_conn() -> Iterator[sqlite3.Connection]:
    """每个请求一个短连接：SQLite 只读场景下最省心，避免跨线程复用。

    注意 ``check_same_thread=False``：FastAPI 对同步生成器依赖走
    ``contextmanager_in_threadpool``，同一次请求里 ``connect()``、端点查询体、
    ``conn.close()`` 会分别落到线程池的**不同线程**（串行执行、不并发）。若沿用
    SQLite 默认的同线程限制，并发请求会随机抛 ``ProgrammingError`` 并返回 500。
    每个请求独占连接，任一时刻只有一个线程在访问它，因此这里放开限制是安全的。
    """
    conn = connect(create=False, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


Conn = Depends(get_conn)


# --------------------------------------------------------------------------- #
# 响应装饰：补上前端需要的派生字段
# --------------------------------------------------------------------------- #
def _image_local_url(image: Optional[str]) -> Optional[str]:
    """原图在本地缓存中的访问地址（文件名含中文，需 URL 编码）。"""
    if not image:
        return None
    return f"/images/{quote(str(image))}"


def _source_url(notice_id: Optional[str], notice_title: Optional[str]) -> str:
    """「查看原文」链接：优先详情页模板，否则回退到门户关键词检索地址。"""
    if _PORTAL_DETAIL_URL and notice_id:
        return _PORTAL_DETAIL_URL.format(notice_id=notice_id, keyword=quote(notice_title or ""))
    if notice_title:
        return _PORTAL_LIST_URL.format(keyword=quote(str(notice_title)), notice_id=notice_id or "")
    return _PORTAL_HOME


def _decorate(item: Dict[str, Any]) -> Dict[str, Any]:
    """给一条记录补上图片地址与来源链接（列表与详情共用）。"""
    item["image_local_url"] = _image_local_url(item.get("image"))
    item["source_url"] = _source_url(item.get("notice_id"), item.get("notice_title"))
    return item


def _decorate_list_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """列表项：去掉正文原文（体积大），正文预览走 ``highlight.snippet``。"""
    _decorate(item)
    item.pop("raw_text", None)
    item.pop("evidence", None)
    return item


# --------------------------------------------------------------------------- #
# 应用工厂
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    app = FastAPI(
        title="选调生去向信息检索系统",
        version=str(get(["project", "version"], "0.1.0")),
        description="基于通知公告图片 OCR 的选调生去向检索：关键词/拼音检索、多条件筛选、排序分页、高亮、统计。",
    )

    origins = get(["web", "cors_origins"], None) or [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _revalidate_frontend(request: Request, call_next):
        """前端页面/脚本/样式强制协商缓存（``no-cache`` = 缓存但每次回源校验）。

        Starlette 的 ``StaticFiles`` 只发 ``ETag`` / ``Last-Modified``、不发
        ``Cache-Control``，浏览器于是按「启发式缓存」把 ``app.js`` 长期压在本地。
        结果改了前端代码后用户刷新页面仍在跑旧脚本——「搜索框输入后仍返回全部
        记录」「筛选器展开更多点了没反应」两个 bug 的修复就曾被旧缓存掩盖过一次。
        文件没变时回源只会得到一个 304，成本可忽略；``/images`` 下是大图，保持可缓存。
        """
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/images/"):
            return response
        if path in ("", "/") or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # ---------------- 前端启动字典 ---------------- #
    @app.get("/api/config", summary="前端字典：维度、排序、分页")
    def api_config() -> Dict[str, Any]:
        return {
            "project": get(["project", "name"], "选调生去向信息检索系统"),
            "version": get(["project", "version"], "0.1.0"),
            "dimensions": [{"key": k, "label": v} for k, v in DIMENSION_LABELS.items()],
            "sorts": [{"value": k, "label": v} for k, v in SORT_LABELS.items()],
            "default_page_size": DEFAULT_PAGE_SIZE,
            "max_page_size": MAX_PAGE_SIZE,
            "portal_home": _PORTAL_HOME,
            # 模糊匹配词表（人工维护）的加载情况，便于前端展示与排障
            "fuzzy": fuzzy_terms.summary(),
        }

    # ---------------- 检索 ---------------- #
    @app.get("/api/search", summary="检索（关键词/拼音 + 筛选 + 排序 + 分页 + 高亮）")
    def api_search(
        request: Request,
        q: str = "",
        sort: str = "",
        page: int = 1,
        page_size: int = 0,
        conn: sqlite3.Connection = Conn,
    ) -> Dict[str, Any]:
        # 同名字段重复出现 → 组内 OR；未知维度直接忽略
        filters: Dict[str, List[str]] = {}
        for dimension in DIMENSION_LABELS:
            values = [v for v in request.query_params.getlist(dimension) if v != ""]
            if values:
                filters[dimension] = values

        result = search_query(
            conn,
            q,
            filters=filters or None,
            sort=sort,
            page=page,
            page_size=page_size or None,
        )
        result["items"] = [_decorate_list_item(item) for item in result["items"]]
        return result

    # ---------------- 详情 ---------------- #
    @app.get("/api/record/{record_id}", summary="记录详情")
    def api_record(record_id: int, conn: sqlite3.Connection = Conn) -> Dict[str, Any]:
        detail = get_detail(conn, record_id)
        if not detail:
            raise HTTPException(status_code=404, detail=f"记录不存在：{record_id}")
        return _decorate(detail)

    # ---------------- 分面 / 统计 / 补全 ---------------- #
    @app.get("/api/facets", summary="分面计数（可带关键词与筛选）")
    def api_facets(
        request: Request,
        q: str = "",
        limit: int = 200,
        conn: sqlite3.Connection = Conn,
    ) -> Dict[str, Any]:
        filters = {
            dimension: [v for v in request.query_params.getlist(dimension) if v != ""]
            for dimension in DIMENSION_LABELS
        }
        filters = {k: v for k, v in filters.items() if v}
        return {
            "facets": facet_counts(
                conn, q, filters=filters or None, limit=max(1, min(limit, 500))
            )
        }

    @app.get("/api/stats", summary="统计分析")
    def api_stats(top_n: int = 15, conn: sqlite3.Connection = Conn) -> Dict[str, Any]:
        data = stats_overview(conn, top_n=max(1, min(top_n, 50)))
        # 学历字段来自派生列，便于前端画环形图
        data["degree_level"] = [
            {"value": r["value"], "count": r["count"]}
            for r in data.get("degree_level", [])
        ]
        return data

    @app.get("/api/suggest", summary="搜索框自动补全")
    def api_suggest(conn: sqlite3.Connection = Conn) -> Dict[str, Any]:
        return suggestions(conn)

    # ---------------- 体检 ---------------- #
    @app.get("/api/health", summary="健康检查")
    def api_health(conn: sqlite3.Connection = Conn) -> Dict[str, Any]:
        info = stats(conn)
        return {
            "status": "ok",
            "db_path": str(default_db_path()),
            "stats": info,
        }

    # ---------------- 静态资源 ---------------- #
    if IMAGE_DIR.exists():
        app.mount("/images", StaticFiles(directory=str(IMAGE_DIR)), name="images")
    else:  # pragma: no cover - 仅在数据尚未下载时触发
        logger.warning("原图目录不存在，/images 未挂载：%s", IMAGE_DIR)

    if FRONTEND_DIR.exists():
        # html=True：访问 / 时自动返回 index.html
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    else:  # pragma: no cover

        @app.get("/", include_in_schema=False)
        def _no_frontend() -> JSONResponse:
            return JSONResponse(
                {"detail": "前端目录尚未创建", "expected": str(FRONTEND_DIR)},
                status_code=501,
            )

    return app


app = create_app()

__all__ = ["app", "create_app", "get_conn", "FRONTEND_DIR", "IMAGE_DIR"]
