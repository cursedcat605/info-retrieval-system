"""OCR 结果 → 清洗文本 + 结构化记录。

流程::

    data/interim/ocr_text/*.json   （OCR 原始结果，含坐标与置信度）
        └─ clean_lines / normalize_text          （只做减法：去噪、去页脚、全角转半角）
        └─ apply_line_corrections                （人工纠错层·文本层，改完立刻生效）
        └─ extract_from_ocr_result               （按阅读顺序切分主讲人区块，抽 7 个字段）
            └─ apply_field_corrections           （人工纠错层·字段层，直接改抽取值）
            ├─ data/processed/ocr_clean.txt       清洗后文本（人工核对用）
            ├─ data/processed/images.jsonl        图片级元数据（含 OCR 统计）
            ├─ data/processed/selects_records.jsonl  记录级结构化数据
            ├─ data/processed/selects_records.csv    同上，CSV 版本
            └─ data/processed/_extract_summary.json  汇总统计

人工纠错规则写在 ``config/corrections.yaml``，详见
:mod:`src.preprocess.corrections`。

用法::

    python scripts/extract_records.py
    python scripts/extract_records.py --limit 5      # 抽样验证
    python scripts/extract_records.py --no-corrections  # 看纠错层到底改了什么
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.nlp.field_extractor import ExtractedRecord, extract_from_ocr_result  # noqa: E402
from src.ocr.batch import load_image_metadata, ocr_output_dir  # noqa: E402
from src.ocr.engine import OcrResult  # noqa: E402
from src.preprocess.cleaner import clean_lines  # noqa: E402
from src.preprocess.corrections import (  # noqa: E402
    apply_field_corrections,
    apply_line_corrections,
    load_corrections,
)
from src.utils.logger import get_logger  # noqa: E402
from src.utils.paths import data_dir, ensure_dir  # noqa: E402

logger = get_logger("extract_records")

#: 写入 CSV 的列顺序（raw_text 体积大，只进 JSONL）
RECORD_FIELDS = (
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
)

IMAGE_FIELDS = (
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
    "record_count",
)


def iter_ocr_files(directory: Path, limit: int | None = None) -> List[Path]:
    files = sorted(p for p in directory.glob("*.json") if not p.name.startswith("_"))
    return files[:limit] if limit else files


def record_to_row(record: ExtractedRecord, image: str, index: int) -> Dict[str, Any]:
    data = record.as_dict()
    row = {
        "image": image,
        "block_index": index,
        "name": data.get("name"),
        "cohort": data.get("cohort"),
        "cohort_year": data.get("cohort_year"),
        "college": data.get("college"),
        "major": data.get("major"),
        "destination_org": data.get("destination_org"),
        "city": data.get("city"),
        "province": data.get("province"),
        "position": data.get("position"),
        "degree": data.get("degree"),
        "confidence": data.get("confidence"),
        "field_count": data.get("field_count"),
        "evidence": data.get("evidence"),
        # 该记录所在文本区块的原文（evidence 已是区块全文，最长 400 字符）
        "raw_text": data.get("evidence"),
    }
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 结果 → 清洗文本 + 结构化记录")
    parser.add_argument("--ocr-dir", type=Path, default=None, help="OCR 结果目录")
    parser.add_argument("--out-dir", type=Path, default=None, help="输出目录（默认 data/processed）")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个结果文件")
    parser.add_argument("--min-fields", type=int, default=1, help="至少命中几个核心字段才保留")
    parser.add_argument(
        "--corrections",
        type=Path,
        default=None,
        help=f"人工纠错规则文件（默认 config/corrections.yaml）",
    )
    parser.add_argument(
        "--no-corrections",
        action="store_true",
        help="忽略纠错层，只跑规则/正则（用于对比纠错层到底改了什么）",
    )
    args = parser.parse_args()

    ocr_dir = args.ocr_dir.resolve() if args.ocr_dir else ocr_output_dir()
    out_dir = ensure_dir(args.out_dir.resolve() if args.out_dir else data_dir("processed"))

    files = iter_ocr_files(ocr_dir, args.limit)
    if not files:
        logger.error("没有找到 OCR 结果：%s（请先运行 scripts/run_ocr.py）", ocr_dir)
        return 1

    metadata = load_image_metadata()
    corrections = None if args.no_corrections else load_corrections(args.corrections)
    line_fixed = 0
    field_fixed: Counter[str] = Counter()
    image_rows: List[Dict[str, Any]] = []
    record_rows: List[Dict[str, Any]] = []
    clean_chunks: List[str] = []
    field_fill: Counter[str] = Counter()
    failed = 0

    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("跳过损坏的 OCR 结果 %s：%s", path.name, exc)
            failed += 1
            continue

        result = OcrResult.from_dict(payload)
        # 人工纠错层（文本层）：就地改写 result.lines，
        # 因此下游的字段抽取与 ocr_clean.txt 都会看到纠正后的文本。
        line_fixed += apply_line_corrections(result, corrections)
        meta = dict(payload.get("meta") or {})
        meta.update(metadata.get(result.image or path.stem, {}))

        records: List[ExtractedRecord] = []
        if result.ok and result.lines:
            records = [
                r for r in extract_from_ocr_result(result) if r.field_count >= args.min_fields
            ]

        # 清洗后文本（保留一份纯文本，便于人工核对与关键词兜底检索）
        cleaned = clean_lines(result.text) if result.text else []
        if cleaned:
            clean_chunks.append(f"===== {result.image} =====\n" + "\n".join(cleaned))

        image_rows.append(
            {
                "image": result.image or path.stem,
                "stem": path.stem,
                "notice_id": meta.get("notice_id"),
                "notice_title": meta.get("notice_title"),
                "organization": meta.get("organization"),
                "release_time": meta.get("release_time"),
                "notice_type": meta.get("notice_type"),
                "image_url": meta.get("image_url"),
                "alt": meta.get("alt"),
                "sha256": meta.get("sha256"),
                "size_bytes": meta.get("size_bytes"),
                "local_path": meta.get("local_path") or result.image_path,
                "ocr_engine": result.engine,
                "ocr_line_count": len(result.lines),
                "ocr_mean_score": round(result.mean_score, 4),
                "ocr_elapsed": round(result.elapsed, 3),
                "ocr_error": result.error,
                "record_count": len(records),
            }
        )

        for idx, record in enumerate(records):
            row = record_to_row(record, image_rows[-1]["image"], idx)
            # 人工纠错层（字段层）：直接覆盖最终取值，
            # 用于"文本没错、是切分规则切错了"的情况。
            for field in apply_field_corrections(row, corrections):
                field_fixed[field] += 1
            record_rows.append(row)
            for field in ("name", "cohort", "college", "major", "destination_org", "city", "position"):
                if row.get(field):
                    field_fill[field] += 1

    # ---- 落盘 ----
    (out_dir / "ocr_clean.txt").write_text("\n\n".join(clean_chunks), encoding="utf-8")

    with (out_dir / "images.jsonl").open("w", encoding="utf-8") as fh:
        for row in image_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with (out_dir / "selects_records.jsonl").open("w", encoding="utf-8") as fh:
        for row in record_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with (out_dir / "selects_records.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(RECORD_FIELDS))
        writer.writeheader()
        for row in record_rows:
            writer.writerow({k: row.get(k) for k in RECORD_FIELDS})

    total = len(record_rows)
    hit_rules: List[str] = []
    miss_rules: List[str] = []
    if corrections is not None:
        corrections.check_images(r["image"] for r in image_rows)
        hit_rules, miss_rules = corrections.report()
    summary = {
        "ocr_files": len(files),
        "ocr_bad_files": failed,
        "images": len(image_rows),
        "images_with_records": sum(1 for r in image_rows if r["record_count"]),
        "records": total,
        "fill_rate": {k: round(field_fill[k] / total, 3) if total else 0.0 for k in field_fill},
        "core_fields": ["name", "cohort", "college", "major", "destination_org", "city", "position"],
        "corrections": {
            "enabled": corrections is not None,
            "source": str(corrections.source_path) if corrections is not None else None,
            "text_rules": len(corrections.text_rules) if corrections is not None else 0,
            "field_rules": len(corrections.field_rules) if corrections is not None else 0,
            "lines_changed": line_fixed,
            "fields_changed": dict(field_fixed),
            "matched": hit_rules,
            "unmatched": miss_rules,
            "warnings": list(corrections.warnings) if corrections is not None else [],
        },
    }
    (out_dir / "_extract_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "抽取完成：%d 个 OCR 结果 → %d 张图片、%d 条记录，输出目录 %s",
        len(files), len(image_rows), total, out_dir,
    )
    if corrections is not None and not corrections.is_empty:
        logger.info(
            "纠错层：改写 %d 行文本、覆盖 %d 个字段；命中 %d 条规则，未命中 %d 条",
            line_fixed, sum(field_fixed.values()), len(hit_rules), len(miss_rules),
        )
        for message in miss_rules:
            logger.warning("纠错规则未命中任何数据：%s", message)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
