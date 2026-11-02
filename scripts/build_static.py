"""把检索系统导出为**纯静态站点**，用于 GitHub Pages 托管。

产物目录（默认 ``docs/``）::

    docs/
      .nojekyll                 关闭 Jekyll，保证 api/ 与含中文的文件名可访问
      index.html search.html stats.html detail.html styles.css
      common.js app.js home.js detail.js stats.js
      static-api.js             浏览器端检索内核（拦截 /api/* 请求）
      vendor/echarts.min.js     统计页图表库（内置，教室网络也能出图）
      api/bootstrap.json        一次性预渲染的全量数据（记录 + 统计 + 词表）
      media/<stem>.webp         原图（宽 ≤1000，质量 82，体积约原图 1/6）

设计要点
--------
后端 7 个只读接口在导出时全部**预渲染**成 JSON，浏览器端只需实现「筛选 / 排序 /
分页 / 高亮」这些依赖用户输入的环节，因此 :mod:`src.search.engine` 里的 SQL 逻辑
不需要逐条翻译，静态站与本地开发站共用同一份前端代码。

导出后所有根路径（``/styles.css``、``/api`` …）会被改写成相对路径，
这样站点挂在 ``https://<user>.github.io/<repo>/`` 这类子目录下也不会 404。
前端唯一的外部依赖（ECharts 的 jsDelivr 地址）会被改成本地 ``vendor/`` 副本，
静态站因此可以完全离线运行。
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.repo import (  # noqa: E402
    FACET_DIMENSIONS,
    SORT_ORDERS,
    connect,
    get_record,
    is_manual_source,
    stats as db_stats,
    stats_overview,
)
from src.search import DEFAULT_PAGE_SIZE, DIMENSION_LABELS, MAX_PAGE_SIZE  # noqa: E402
from src.search import SORT_LABELS  # noqa: E402
from src.search import fuzzy as fuzzy_terms  # noqa: E402
from src.search import suggestions as build_suggestions  # noqa: E402
from src.utils.config import get  # noqa: E402

# ``web.backend.__init__`` 把模块属性 ``app`` 重绑定成了 FastAPI 实例，
# 这里必须按模块路径导入才能拿到 ``_decorate`` 等内部工具函数
web_app = importlib.import_module("web.backend.app")

DEFAULT_OUT = ROOT / "docs"
FRONTEND_DIR = ROOT / "web" / "frontend"
STATIC_DIR = ROOT / "web" / "static"
SOURCE_IMAGE_DIR = ROOT / "data" / "raw" / "images"

#: 前端引用的第三方 CDN 地址 → 站点内本地副本（教室网络常常连不上 jsDelivr）
VENDOR_REWRITES: Dict[str, str] = {
    "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js": "vendor/echarts.min.js",
}

#: ``web/static/vendor/`` 下需要整体拷进站点的第三方库
VENDOR_DIR = STATIC_DIR / "vendor"

#: 需要从 ``web/frontend`` 拷进站点的文件（preview.html 是设计稿，不发布）
FRONTEND_FILES: Tuple[str, ...] = (
    "index.html",
    "search.html",
    "stats.html",
    "detail.html",
    "styles.css",
    "common.js",
    "app.js",
    "home.js",
    "detail.js",
    "stats.js",
)

#: 站点内可能以根路径出现的入口；改写成相对路径时按此白名单匹配，
#: 避免误伤 ``"/api"`` 之外的普通字符串（如正则、URL 片段）
SITE_PATHS: Tuple[str, ...] = (
    "api",
    "media",
    "images",
    "thumbs",
    "index.html",
    "search.html",
    "stats.html",
    "detail.html",
    "preview.html",
    "styles.css",
    "common.js",
    "app.js",
    "home.js",
    "detail.js",
    "stats.js",
    "static-api.js",
)

#: 列表项需要下发给前端的字段（与 ``app._decorate_list_item`` 对齐）
LIST_KEYS: Tuple[str, ...] = (
    "id",
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
    "position_category",
    "degree_level",
    "name_pinyin",
    "name_initials",
    "confidence",
    "field_count",
    "image",
    "image_local_url",
    "source_url",
    "notice_id",
    "notice_title",
    "organization",
    "release_time",
)

#: 详情页额外需要的字段（``get_detail`` 相对列表项多出来的部分）
DETAIL_EXTRA_KEYS: Tuple[str, ...] = (
    "block_index",
    "image_url",
    "stem",
    "local_path",
    "alt",
    "evidence",
    "notice_type",
    "size_bytes",
    "ocr_engine",
    "ocr_line_count",
    "ocr_mean_score",
    "ocr_elapsed",
    "ocr_error",
)

#: 原文图片导出参数：按**宽度**缩放（公示截图普遍是窄而长的滚动长图，
#: 按最长边缩放会把宽度压到 600 以内，表头小字直接糊掉）
MEDIA_MAX_WIDTH = 1000
MEDIA_QUALITY = 82


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def _write_json(path: Path, payload: Any, *, compact: bool = True) -> int:
    """写 JSON（默认压缩成一行，体积比缩进小 15% 左右），返回字符数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _relativize(text: str) -> str:
    """把 ``href="/styles.css"`` 这类根路径改写成相对路径。

    只处理白名单里的入口，且要求前一个字符是引号或括号，
    这样 ``"..."`` 中间偶然出现的 ``/api`` 不会被误改。
    """
    for name in sorted(SITE_PATHS, key=len, reverse=True):
        # href="/xxx"  src='/xxx'  ("/xxx")  →  去掉前导斜杠
        text = re.sub(
            r'(?<=["\'(])/' + re.escape(name) + r'(?=["\'/?#])',
            name,
            text,
        )
    # 顶栏 ``href="/"``：指的是站点根，静态站里统一落到首页
    text = text.replace('href="/"', 'href="index.html"')
    text = text.replace("href='/'", "href='index.html'")
    return text


# --------------------------------------------------------------------------- #
# 原图：转成 WebP（宽 ≤1000）
# --------------------------------------------------------------------------- #
def _export_media(conn, out_dir: Path, *, force: bool = False) -> Dict[str, str]:
    """把原图批量转成 WebP，返回 ``{image 字段: 站点内相对地址}``。"""
    from PIL import Image, ImageOps

    media_dir = out_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    rows = conn.execute("SELECT image, stem FROM images").fetchall()
    mapping: Dict[str, str] = {}
    made = skipped = missing = 0
    for row in rows:
        image = row["image"]
        if not image or is_manual_source(image):
            continue
        stem = row["stem"] or Path(str(image)).stem
        target = media_dir / f"{stem}.webp"
        mapping[image] = f"media/{stem}.webp"
        if target.exists() and not force:
            skipped += 1
            continue
        source = SOURCE_IMAGE_DIR / str(image)
        if not source.exists():
            missing += 1
            mapping.pop(image, None)
            continue
        try:
            with Image.open(source) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGBA")
                    canvas = Image.new("RGB", img.size, (255, 255, 255))
                    canvas.paste(img, mask=img.split()[-1])
                    img = canvas
                else:
                    img = img.convert("RGB")
                if img.width > MEDIA_MAX_WIDTH:
                    height = round(img.height * MEDIA_MAX_WIDTH / img.width)
                    img = img.resize((MEDIA_MAX_WIDTH, height), Image.LANCZOS)
                img.save(target, "WEBP", quality=MEDIA_QUALITY, method=5)
            made += 1
        except Exception as exc:  # 单张失败不影响整体导出
            missing += 1
            mapping.pop(image, None)
            print(f"  ! 转换失败：{image}（{exc}）")
    total = sum(p.stat().st_size for p in media_dir.glob("*.webp"))
    print(
        f"  原图：新增 {made} / 复用 {skipped} / 跳过 {missing}"
        f"，media/ 共 {total / 1024 / 1024:.1f} MB"
    )
    return mapping


def _prune_media(out_dir: Path, keep: Iterable[str]) -> int:
    """删除已无记录引用的 WebP（重跑导出时保持一致）。"""
    media_dir = out_dir / "media"
    if not media_dir.exists():
        return 0
    wanted = {Path(name).name for name in keep}
    removed = 0
    for path in media_dir.glob("*.webp"):
        if path.name not in wanted:
            path.unlink()
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
# 数据预渲染
# --------------------------------------------------------------------------- #
def _list_item(record: Dict[str, Any], media: Dict[str, str]) -> Dict[str, Any]:
    """一条记录 → 列表项（字段白名单 + 图片地址换成站点相对路径）。"""
    item: Dict[str, Any] = {}
    for key in LIST_KEYS:
        value = record.get(key)
        if isinstance(value, str) and not value.strip():
            value = None
        if value is not None:
            item[key] = value
    image = record.get("image")
    item["image_local_url"] = media.get(image) if image else None
    # 后端 ``_decorate`` 的 ``source_url`` 恒为字符串（无来源时是空串）
    item["source_url"] = record.get("source_url") or ""
    # 列表页只在高亮片段里用到正文，正文本身单独挂在 ``rt`` 上（见 build_site）
    item["raw_text"] = record.get("raw_text") or None
    return item


def _detail_extra(record: Dict[str, Any]) -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    for key in DETAIL_EXTRA_KEYS:
        value = record.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        extra[key] = value
    return extra


def build_bootstrap(conn, media: Dict[str, str]) -> Dict[str, Any]:
    """一次性打包静态站需要的全部数据。"""
    ids = [row["id"] for row in conn.execute("SELECT id FROM selects_records ORDER BY id")]
    records: List[Dict[str, Any]] = []
    details: Dict[str, Any] = {}
    for rid in ids:
        record = get_record(conn, rid)
        if not record:
            continue
        # 复用后端同一套装饰逻辑，保证 source_url / 图片地址与在线版一致
        web_app._decorate(record)
        records.append(_list_item(record, media))
        extra = _detail_extra(record)
        if extra:
            details[str(rid)] = extra

    health_stats = dict(db_stats(conn))
    if web_app._DATA_UPDATED:
        health_stats["data_updated"] = web_app._DATA_UPDATED

    overview = stats_overview(conn, top_n=50)
    overview["degree_level"] = [
        {"value": row["value"], "count": row["count"]}
        for row in overview.get("degree_level", [])
    ]

    config = {
        "project": get(["project", "name"], "选调生去向信息检索系统"),
        "version": get(["project", "version"], "0.1.0"),
        "dimensions": [
            {"key": key, "label": label} for key, label in DIMENSION_LABELS.items()
        ],
        "sorts": [{"value": key, "label": label} for key, label in SORT_LABELS.items()],
        "default_page_size": DEFAULT_PAGE_SIZE,
        "max_page_size": MAX_PAGE_SIZE,
        "portal_home": web_app._PORTAL_HOME,
        "fuzzy": {
            key: value
            for key, value in fuzzy_terms.summary().items()
            if key != "path"  # 不把本机绝对路径写进公开站点
        },
        "static": True,
    }

    suggest = build_suggestions(conn)
    # 模糊词表的加载详情里带着本机绝对路径，公开站点上没意义，去掉
    if isinstance(suggest.get("fuzzy"), dict):
        suggest["fuzzy"].pop("path", None)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": config,
        "suggest": suggest,
        "stats": overview,
        "health": {
            "status": "ok",
            "db_path": "静态站点（数据在浏览器端检索）",
            "stats": health_stats,
        },
        # 词表按「一组等价写法」导出，浏览器端据此做与 fuzzy.expand 相同的扩展
        "fuzzy_groups": [list(group.terms) for group in fuzzy_terms.load_groups()],
        "dimensions": [{"key": key, "label": label} for key, label in FACET_DIMENSIONS],
        "sort_options": [
            {"value": key, "label": SORT_LABELS.get(key, key)} for key in SORT_ORDERS
        ],
        "records": records,
        "details": details,
    }


# --------------------------------------------------------------------------- #
# 前端拷贝
# --------------------------------------------------------------------------- #
def copy_frontend(out_dir: Path, static_assets: Path) -> None:
    """把 ``web/frontend`` 拷进站点，并把根路径改写成相对路径。"""
    for name in FRONTEND_FILES:
        source = FRONTEND_DIR / name
        if not source.exists():
            print(f"  ! 缺少前端文件：{source}")
            continue
        text = source.read_text(encoding="utf-8")
        text = _relativize(text)
        text = _localize_vendor(text)
        if name.endswith(".html"):
            text = _inject_static_api(text)
        (out_dir / name).write_text(text, encoding="utf-8")

    shutil.copy2(static_assets, out_dir / "static-api.js")

    if VENDOR_DIR.exists():
        target = out_dir / "vendor"
        target.mkdir(parents=True, exist_ok=True)
        for source in VENDOR_DIR.iterdir():
            if source.is_file():
                shutil.copy2(source, target / source.name)


def _localize_vendor(text: str) -> str:
    """把第三方 CDN 地址换成站点内的本地副本，让静态站彻底离线可用。"""
    for remote, local in VENDOR_REWRITES.items():
        if remote in text and not (VENDOR_DIR / Path(local).name).exists():
            print(f"  ! 缺少本地副本：{VENDOR_DIR / Path(local).name}，保留 CDN 地址")
            continue
        text = text.replace(remote, local)
    return text


def _inject_static_api(html: str) -> str:
    """在每个页面的 ``common.js`` **之前**插入 static-api.js。

    静态模式下 ``IRS`` 的取数接口被整体替换成浏览器端实现，因此必须先于
    ``common.js`` 装载；标签用 ``data-irs-replace`` 标记，便于重复导出时幂等。
    """
    if "static-api.js" in html:
        return html
    return re.sub(
        r'(<script src="common\.js[^"]*"></script>)',
        r'<script src="static-api.js"></script>\n\1',
        html,
        count=1,
    )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def build_site(out_dir: Path, *, force: bool = False, prune: bool = True) -> None:
    static_assets = ROOT / "web" / "static" / "static-api.js"
    if not static_assets.exists():
        raise SystemExit(f"缺少 static-api.js：{static_assets}")

    if out_dir.exists():
        # 只清理受管文件，避免误删用户放在 docs/ 里的 CNAME 之类
        for pattern in ("*.html", "*.css", "*.js"):
            for path in out_dir.glob(pattern):
                if path.name != "static-api.js":
                    path.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 连接数据库…")
    conn = connect(create=False)
    try:
        print(f"[2/4] 导出原图 → {out_dir / 'media'}")
        media = _export_media(conn, out_dir, force=force)
        if prune:
            removed = _prune_media(out_dir, media.values())
            if removed:
                print(f"  清理无引用的 WebP：{removed} 个")

        print(f"[3/4] 预渲染接口数据…")
        payload = build_bootstrap(conn, media)
    finally:
        conn.close()

    size = _write_json(out_dir / "api" / "bootstrap.json", payload)
    print(
        f"  api/bootstrap.json：{size / 1024:.0f} KB"
        f"（记录 {len(payload['records'])} 条 / 详情 {len(payload['details'])} 条）"
    )

    print(f"[4/4] 拷贝前端…")
    copy_frontend(out_dir, static_assets)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    print(f"\n完成：{out_dir}")
    print(f"  站点合计 {total / 1024 / 1024:.1f} MB")
    print(f"  本地预览：python -m http.server 8000 --directory \"{out_dir.name}\"")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="导出静态站点（GitHub Pages）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录，默认 docs/")
    parser.add_argument("--force", action="store_true", help="重新转码全部原图")
    parser.add_argument("--no-prune", action="store_true", help="保留无引用的 WebP")
    args = parser.parse_args(argv)

    build_site(Path(args.out), force=args.force, prune=not args.no_prune)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
