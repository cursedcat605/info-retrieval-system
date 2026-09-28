"""批量 OCR：扫描 ``data/raw/images/`` → 识别 → 写入 ``data/interim/ocr_text/``。

产物（每张图片两份，互为补充）：

- ``<stem>.txt``  —— 纯文本行，供清洗/抽取阶段直接读取；
- ``<stem>.json`` —— 结构化结果（每行文本 + 置信度 + 文本框 + 耗时 + 引擎），
  并附带该图片在 ``data/raw/metadata/`` 中的溯源信息。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..utils.config import get, resolve_path
from ..utils.logger import get_logger
from ..utils.paths import data_dir, ensure_dir, project_root
from .engine import BaseOcrEngine, OcrResult, create_engine, enhance_for_ocr, load_image

logger = get_logger("ocr.batch")

DEFAULT_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


# --------------------------------------------------------------------------- #
# 路径与元数据
# --------------------------------------------------------------------------- #
def images_dir() -> Path:
    return data_dir("raw", "images")


def metadata_dir() -> Path:
    return data_dir("raw", "metadata")


def ocr_output_dir() -> Path:
    configured = get(["ocr", "output_dir"], "data/interim/ocr_text")
    return ensure_dir(resolve_path(str(configured)))


def iter_images(directory: Optional[Path] = None, exts: Sequence[str] = DEFAULT_EXTS) -> List[Path]:
    """列出待识别的图片，按文件名排序，保证多次运行顺序一致。"""
    root = Path(directory) if directory else images_dir()
    if not root.exists():
        return []
    normalized = {e.lower() for e in exts}
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in normalized]
    return sorted(files, key=lambda p: p.name)


def load_image_metadata(directory: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """汇总 ``data/raw/metadata/notice_images_*.json``，返回 ``文件名 -> 元数据`` 映射。

    用于把「图片来自哪条通知、发布时间、发布单位」等信息带入数据库，
    让检索结果可以回溯到原始公告。
    """
    root = Path(directory) if directory else metadata_dir()
    mapping: Dict[str, Dict[str, Any]] = {}
    if not root.exists():
        return mapping

    for json_file in sorted(root.glob("notice_images_*.json")):
        try:
            payload = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("读取元数据失败 %s：%s", json_file.name, exc)
            continue
        for record in payload.get("records", []) or []:
            local_path = record.get("local_path") or ""
            name = Path(str(local_path).replace("\\", "/")).name
            if not name:
                continue
            mapping[name] = {
                "notice_id": record.get("notice_id"),
                "notice_title": record.get("notice_title"),
                "organization": record.get("organization"),
                "release_time": record.get("release_time"),
                "notice_type": record.get("notice_type"),
                "image_url": record.get("image_url"),
                "alt": record.get("alt"),
                "sha256": record.get("sha256"),
                "metadata_source": json_file.name,
            }
    logger.info("已载入 %d 条图片元数据（来自 %s）", len(mapping), root)
    return mapping


def parse_filename_meta(path: Path) -> Dict[str, Any]:
    """从文件名 ``日期_通知ID_序号_图注.ext`` 中解析兜底元数据。"""
    parts = path.stem.split("_")
    meta: Dict[str, Any] = {"filename_date": None, "notice_id": None, "seq": None, "alt": None}
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        meta["filename_date"] = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:]}"
    if len(parts) >= 2 and parts[1].isdigit():
        meta["notice_id"] = parts[1]
    if len(parts) >= 3 and parts[2].isdigit():
        meta["seq"] = int(parts[2])
    if len(parts) >= 4:
        meta["alt"] = "_".join(parts[3:])
    return meta


# --------------------------------------------------------------------------- #
# 结果落盘
# --------------------------------------------------------------------------- #
def result_paths(stem: str, out_dir: Optional[Path] = None) -> tuple[Path, Path]:
    out = out_dir or ocr_output_dir()
    return out / f"{stem}.txt", out / f"{stem}.json"


def save_result(result: OcrResult, extra: Optional[Dict[str, Any]] = None, out_dir: Optional[Path] = None) -> tuple[Path, Path]:
    """写入 ``.txt`` 与 ``.json`` 两份产物，返回两者路径。"""
    stem = Path(result.image).stem
    txt_path, json_path = result_paths(stem, out_dir)
    txt_path.write_text(result.text, encoding="utf-8")

    payload = result.to_dict()
    if extra:
        payload["meta"] = extra
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return txt_path, json_path


def load_result(stem: str, out_dir: Optional[Path] = None) -> Optional[OcrResult]:
    """读取已保存的 JSON 结果；不存在或损坏时返回 ``None``。"""
    _txt_path, json_path = result_paths(stem, out_dir)
    if not json_path.exists():
        return None
    try:
        return OcrResult.from_dict(json.loads(json_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 OCR 结果失败 %s：%s", json_path.name, exc)
        return None


# --------------------------------------------------------------------------- #
# 批处理
# --------------------------------------------------------------------------- #
@dataclass
class BatchSummary:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    empty: int = 0
    elapsed: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "processed": self.processed,
            "skipped": self.skipped,
            "failed": self.failed,
            "empty": self.empty,
            "elapsed": round(self.elapsed, 2),
        }


def run_batch(
    directory: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    engine: Optional[BaseOcrEngine] = None,
    engine_name: Optional[str] = None,
    skip_existing: bool = True,
    limit: Optional[int] = None,
    preprocess: bool = True,
    progress: bool = True,
) -> BatchSummary:
    """对目录内所有图片执行 OCR。

    :param skip_existing: 已存在 ``.json`` 结果的图片直接跳过（支持断点续跑）。
    :param limit: 仅处理前 N 张，便于快速验证。
    """
    out = out_dir or ocr_output_dir()
    files = iter_images(directory)
    if limit is not None:
        files = files[:limit]

    summary = BatchSummary(total=len(files))
    if not files:
        logger.warning("未找到待识别图片：%s", directory or images_dir())
        return summary

    if engine is None:
        engine = create_engine(engine_name or get(["ocr", "engine"], "rapidocr"))

    metadata = load_image_metadata()
    root = project_root()
    started = time.perf_counter()
    iterator: Iterable[Path] = files
    if progress:
        try:
            from tqdm import tqdm  # 可选依赖

            iterator = tqdm(files, desc="OCR", unit="img")
        except ImportError:
            iterator = files

    for path in iterator:
        if skip_existing and result_paths(path.stem, out)[1].exists():
            summary.skipped += 1
            continue

        rel_path = str(path.relative_to(root)) if path.is_absolute() else str(path)
        t0 = time.perf_counter()
        try:
            image = load_image(path)
            if preprocess:
                image = enhance_for_ocr(image)
            lines = engine.recognize(image)
            result = OcrResult(
                image=path.name,
                image_path=rel_path.replace("\\", "/"),
                engine=engine.name,
                lines=lines,
                elapsed=time.perf_counter() - t0,
            )
            if not lines:
                summary.empty += 1
                logger.warning("未识别出文字：%s", path.name)
        except Exception as exc:  # noqa: BLE001 - 单张失败不应中断整批
            summary.failed += 1
            logger.error("OCR 失败 %s：%s", path.name, exc)
            result = OcrResult(
                image=path.name,
                image_path=rel_path.replace("\\", "/"),
                engine=engine.name,
                elapsed=time.perf_counter() - t0,
                error=str(exc),
            )

        extra: Dict[str, Any] = parse_filename_meta(path)
        extra.update(metadata.get(path.name, {}))
        save_result(result, extra=extra, out_dir=out)
        summary.processed += 1

        if progress and summary.processed % 20 == 0:
            logger.info("OCR 进度 %d/%d", summary.processed + summary.skipped, summary.total)

    summary.elapsed = time.perf_counter() - started
    logger.info(
        "OCR 完成：共 %d，识别 %d，跳过 %d，失败 %d，空结果 %d，耗时 %.1fs，引擎 %s",
        summary.total,
        summary.processed,
        summary.skipped,
        summary.failed,
        summary.empty,
        summary.elapsed,
        engine.name,
    )
    return summary


__all__ = [
    "BatchSummary",
    "images_dir",
    "metadata_dir",
    "ocr_output_dir",
    "iter_images",
    "load_image_metadata",
    "parse_filename_meta",
    "result_paths",
    "save_result",
    "load_result",
    "run_batch",
]
