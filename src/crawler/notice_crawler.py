"""选调经验分享通知爬虫。

数据来源：中央民族大学信息门户「通知公告」模块后端接口
（comsys-portal-notice-web/getNoticeByPage）。

该接口需要登录会话，因此本模块提供两种获取数据的方式：
1. ``process_api_payload`` / ``--from-json``：处理浏览器已导出的接口 JSON（推荐，
   由 ``collect_service`` 或手动导出获得）；
2. ``fetch_list``：直接携带 Cookie 调用接口（用于可自动化的场景）。

图片本身位于公网可访问的 ``/upload/ueditor/`` 路径下，无需登录即可下载。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from ..utils.logger import get_logger
from ..utils.paths import data_dir, project_root

LOG = get_logger("crawler")

API_ROOT = "https://my.muc.edu.cn/comsys-portal-notice-web"
LIST_API = f"{API_ROOT}/getNoticeByPage"

# UEditor 编辑器插入图片时产生的占位图，需过滤
PLACEHOLDER_MARKERS = (
    "spacer.gif",
    "/static/index/UEditor/",
    "loadingclass",
)

_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_IMAGE_EXT = re.compile(r"\.(jpe?g|png|gif|webp|bmp)$", re.IGNORECASE)


@dataclass
class Notice:
    """一条通知公告。"""

    notice_id: str
    title: str
    organization: str = ""
    release_time: str = ""
    notice_type: str = ""
    content: str = ""


@dataclass
class ImageRecord:
    """一张图片的下载记录（写入元数据用）。"""

    notice_id: str
    notice_title: str
    organization: str
    release_time: str
    notice_type: str
    image_url: str
    alt: str = ""
    local_path: str = ""
    sha256: str = ""
    size_bytes: int = 0
    downloaded_at: str = ""
    status: str = "pending"
    error: str = ""


def normalize_url(url: str) -> str:
    """规范化图片 URL：去掉冗余的默认端口（如 http://host:80/...）。"""
    parsed = urlparse(url)
    netloc = parsed.netloc
    if parsed.scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif parsed.scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    return urlunparse(parsed._replace(netloc=netloc))


def is_placeholder(url: str) -> bool:
    """判断是否为编辑器占位图或无效地址。"""
    if not url or url.startswith("data:"):
        return True
    low = url.lower()
    return any(m.lower() in low for m in PLACEHOLDER_MARKERS)


def extract_images(content_html: str) -> list[tuple[str, str]]:
    """从通知正文 HTML 中提取 (图片URL, alt文本) 列表，已过滤占位图。"""
    if not content_html:
        return []
    soup = BeautifulSoup(content_html, "lxml")
    results: list[tuple[str, str]] = []
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if is_placeholder(src):
            continue
        results.append((normalize_url(src), (img.get("alt") or "").strip()))
    return results


def parse_notices(tables: Iterable[dict[str, Any]]) -> list[Notice]:
    """把接口返回的 tables 列表解析为 Notice 对象列表。"""
    notices: list[Notice] = []
    for row in tables or []:
        notices.append(
            Notice(
                notice_id=str(row.get("notice_id", "")),
                title=(row.get("notice_title") or "").strip(),
                organization=(row.get("organization_name") or "").strip(),
                release_time=(row.get("notice_release_time") or "").strip(),
                notice_type=(row.get("notice_type_name") or "").strip(),
                content=row.get("notice_content") or "",
            )
        )
    return notices


def sanitize_filename(name: str, max_len: int = 60) -> str:
    """清洗文件名中的非法字符并限制长度。"""
    cleaned = _ILLEGAL_FILENAME.sub("_", name).strip(" ._")
    return cleaned[:max_len] or "image"


def strip_image_ext(name: str) -> str:
    """去掉名称末尾的图片扩展名（避免拼出 a.jpg.jpg 这类重复后缀）。"""
    return _IMAGE_EXT.sub("", name).strip(" ._") or "image"


def _guess_ext(url: str, content_type: str = "") -> str:
    """根据 URL 或 Content-Type 推断扩展名。"""
    path = urlparse(url).path
    if "." in Path(path).name:
        ext = Path(path).suffix.lower()
        if 1 < len(ext) <= 5:
            return ext
    ct = (content_type or "").lower()
    for key, ext in (("jpeg", ".jpg"), ("png", ".png"), ("gif", ".gif"),
                     ("webp", ".webp"), ("bmp", ".bmp")):
        if key in ct:
            return ext
    return ".jpg"


class NoticeCrawler:
    """通知图片爬虫：解析数据 → 提取图片 → 下载 → 记录元数据。"""

    def __init__(
        self,
        images_dir: Path | None = None,
        metadata_dir: Path | None = None,
        timeout: int = 30,
        retries: int = 3,
        delay: float = 0.5,
        user_agent: str = "Mozilla/5.0 (compatible; InfoRetrievalBot/0.1)",
    ) -> None:
        self.images_dir = images_dir or data_dir("raw", "images")
        self.metadata_dir = metadata_dir or data_dir("raw", "metadata")
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Referer": "https://my.muc.edu.cn/",
        })

    # ---------- 下载 ----------
    def download_image(self, url: str, dest_stem: str) -> tuple[Path | None, str, int]:
        """下载单张图片，返回 (本地路径, sha256, 字节数)。失败返回 (None, "", 0)。"""
        last_err = ""
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout, stream=True)
                resp.raise_for_status()
                content = resp.content
                if not content:
                    raise ValueError("empty response body")
                ext = _guess_ext(url, resp.headers.get("Content-Type", ""))
                dest = self.images_dir / f"{dest_stem}{ext}"
                dest.write_bytes(content)
                sha = hashlib.sha256(content).hexdigest()
                if self.delay:
                    time.sleep(self.delay)
                return dest, sha, len(content)
            except Exception as exc:  # noqa: BLE001 - 记录并重试
                last_err = str(exc)
                LOG.warning("下载失败(%s/%s) %s：%s", attempt, self.retries, url, exc)
                time.sleep(self.delay * attempt)
        LOG.error("放弃下载 %s：%s", url, last_err)
        return None, "", 0

    # ---------- 主流程 ----------
    def crawl(self, notices: list[Notice]) -> list[ImageRecord]:
        """下载所有通知中的图片（按 URL 全局去重）。"""
        seen: set[str] = set()
        records: list[ImageRecord] = []

        for notice in notices:
            images = extract_images(notice.content)
            if not images:
                LOG.info("【无图片】%s (%s)", notice.title, notice.notice_id)
                continue
            day = (notice.release_time or "").split(" ")[0].replace("-", "") or "unknown"
            for idx, (url, alt) in enumerate(images, start=1):
                if url in seen:
                    LOG.info("【重复跳过】%s", url)
                    continue
                seen.add(url)

                label = strip_image_ext(alt or Path(urlparse(url).path).name)
                stem = f"{day}_{notice.notice_id}_{idx:02d}_{sanitize_filename(label)}"
                dest, sha, size = self.download_image(url, stem)

                rec = ImageRecord(
                    notice_id=notice.notice_id,
                    notice_title=notice.title,
                    organization=notice.organization,
                    release_time=notice.release_time,
                    notice_type=notice.notice_type,
                    image_url=url,
                    alt=alt,
                )
                if dest is not None:
                    rec.local_path = str(dest.relative_to(project_root()))
                    rec.sha256 = sha
                    rec.size_bytes = size
                    rec.downloaded_at = datetime.now().isoformat(timespec="seconds")
                    rec.status = "ok"
                    LOG.info("【已下载】%s -> %s (%d KB)", notice.title, dest.name, size // 1024)
                else:
                    rec.status = "failed"
                    rec.error = "download failed"
                records.append(rec)

        return records

    # ---------- 元数据落盘 ----------
    def save_metadata(self, records: list[ImageRecord], tag: str = "notices") -> tuple[Path, Path]:
        """把下载记录写入 JSON 与 CSV。"""
        slug = sanitize_filename(tag) or "notices"
        json_path = self.metadata_dir / f"notice_images_{slug}.json"
        csv_path = self.metadata_dir / f"notice_images_{slug}.csv"

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "keyword": tag,
            "total": len(records),
            "downloaded": sum(1 for r in records if r.status == "ok"),
            "failed": sum(1 for r in records if r.status != "ok"),
            "records": [asdict(r) for r in records],
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        fields = list(ImageRecord.__dataclass_fields__.keys())
        with csv_path.open("w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()
            for rec in records:
                writer.writerow(asdict(rec))

        LOG.info("元数据已写入：%s / %s", json_path.name, csv_path.name)
        return json_path, csv_path

    def save_raw_payload(self, payload: Any, tag: str = "notices") -> Path:
        """保存接口原始 JSON，便于溯源与重跑。"""
        slug = sanitize_filename(tag) or "notices"
        path = self.metadata_dir / f"notice_api_{slug}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOG.info("原始接口数据已保存：%s", path.name)
        return path

    # ---------- 对外入口 ----------
    def process_api_payload(self, api_json: dict[str, Any], keyword: str = "") -> dict[str, Any]:
        """处理接口返回的完整 JSON：解析 → 下载 → 记录。"""
        datas = (api_json or {}).get("datas") or {}
        tables = datas.get("tables") or []
        page = datas.get("page") or {}

        if not tables:
            LOG.warning("接口未返回任何通知（keyword=%s）", keyword)

        self.save_raw_payload(api_json, tag=keyword or "notices")
        notices = parse_notices(tables)
        LOG.info("共解析到 %d 条通知（接口 total=%s）", len(notices), page.get("total"))
        records = self.crawl(notices)
        self.save_metadata(records, tag=keyword or "notices")

        return {
            "keyword": keyword,
            "notices": len(notices),
            "images": len(records),
            "downloaded": sum(1 for r in records if r.status == "ok"),
            "failed": sum(1 for r in records if r.status != "ok"),
        }

    def fetch_list(
        self,
        keyword: str,
        page_size: int = 50,
        max_pages: int = 20,
        cookies: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """携带 Cookie 直接调用接口，分页取回全部通知（可选自动化模式）。"""
        if cookies:
            self.session.cookies.update(cookies)

        all_rows: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            form = {
                "currentPage": str(page),
                "pageSize": str(page_size),
                "searchValue": keyword,
                "state": "",
                "is_add": "0",
                "totalCounts": "0",
                "total": "-1",
                "select_all": "false",
                "select_notice": "",
                "type": "",
                "searchDepartment": "",
                "start_date": "",
                "end_date": "",
                "system_show": "1",
            }
            resp = self.session.post(LIST_API, data=form, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            rows = ((data.get("datas") or {}).get("tables")) or []
            total = ((data.get("datas") or {}).get("page") or {}).get("total", 0)
            all_rows.extend(rows)
            LOG.info("第 %d 页取回 %d 条（累计 %d / %s）", page, len(rows), len(all_rows), total)
            if not rows or len(all_rows) >= int(total or 0):
                break
        return all_rows

    def crawl_from_cookies(self, keyword: str, cookies: dict[str, str], **kwargs: Any) -> dict[str, Any]:
        """Cookie 模式：抓取列表并下载图片。"""
        rows = self.fetch_list(keyword, cookies=cookies, **kwargs)
        return self.process_api_payload({"datas": {"tables": rows}}, keyword=keyword)
