"""手工整理表格导入：把 Excel/CSV 汇总表写入 ``selects_records``。

与 OCR 流水线的区别只在**数据来源**：字段已经由人工整理好，无需再做文本
抽取；但入库后的约定完全一致——每条记录仍挂在一个 ``images`` 行上，
``block_index`` 在同一来源内唯一，派生列统一由
:func:`src.nlp.derive.apply_derived` 回填。

设计要点：

* **幂等**：来源标识取文件路径（``manual:<相对路径>``），重复导入同一文件会
  先删除该来源的旧记录再写入，不会产生重复行。
* **去重**：默认跳过与库中已有记录重复的行（同名 + 同单位/同省份/同届别），
  同一文件内的重复填报会合并缺失字段，而不是简单丢弃。
* **可重建**：导入后把最终记录另存为 ``data/manual/imported/*.json``，
  ``scripts/build_database.py`` 会一并加载，因此 ``--reset`` 不会丢手工数据。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from ..nlp.derive import apply_derived
from ..nlp.field_extractor import find_region, normalize_cohort_detail
from ..nlp.gazetteer import MUNICIPALITIES, PROVINCE_ALIASES, PROVINCE_FULL
from ..utils.logger import get_logger
from ..utils.paths import ensure_dir, project_root
from .repo import (
    MANUAL_IMAGE_PREFIX,
    is_manual_source,
    rebuild_fts,
    replace_records,
    stats,
    upsert_image,
)

logger = get_logger("manual_import")

#: 表格里的缺失值写法，统一转成 ``None``
MISSING_VALUES: frozenset = frozenset(
    {"", "-", "--", "—", "–", "/", "\\", "无", "暂无", "空", "n/a", "na", "nan", "none", "null"}
)

#: 表头别名 → 数据库字段
_HEADER_ALIASES: Dict[str, str] = {
    "姓名": "name",
    "名字": "name",
    "name": "name",
    "学院": "college",
    "院系": "college",
    "所在学院": "college",
    "college": "college",
    "专业": "major",
    "所学专业": "major",
    "major": "major",
    "去向单位": "destination_org",
    "录用单位": "destination_org",
    "接收单位": "destination_org",
    "单位": "destination_org",
    "去向": "destination_org",
    "destination_org": "destination_org",
    "岗位": "position",
    "职位": "position",
    "职务": "position",
    "position": "position",
    "城市": "city",
    "地市": "city",
    "city": "city",
    "省份": "province",
    "省": "province",
    "province": "province",
    "届别": "cohort",
    "届": "cohort",
    "年级": "cohort",
    "cohort": "cohort",
    "学历": "degree",
    "学位": "degree",
    "degree": "degree",
    "备注": "note",
    "说明": "note",
    "note": "note",
    "remark": "note",
}

#: 判断填充率/记录质量时使用的核心字段
CORE_FIELDS: Tuple[str, ...] = (
    "name",
    "cohort",
    "college",
    "major",
    "destination_org",
    "city",
    "position",
)

#: 同一文件内合并重复行时需要尝试补全的字段
_MERGE_FIELDS: Tuple[str, ...] = (
    "cohort",
    "cohort_year",
    "college",
    "major",
    "destination_org",
    "city",
    "province",
    "position",
    "degree",
    "note",
)

_PUNCTUATION_RE = re.compile(r"[\s\u3000\-—–_·,，。;；:：()（）\[\]【】<>《》/\\|'\"“”‘’]")
_HEADER_SPACE_RE = re.compile(r"[\s\u3000]+")


# --------------------------------------------------------------------------- #
# 值清洗与归一化
# --------------------------------------------------------------------------- #
def clean_value(value: Any) -> Optional[str]:
    """去空白、压缩空格，并把常见缺失值统一转成 ``None``。"""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\u3000", " ").strip()
    text = _HEADER_SPACE_RE.sub(" ", text)
    if text.lower() in MISSING_VALUES:
        return None
    return text or None


def normalize_name(value: Any) -> Optional[str]:
    """姓名去掉所有空白；缺失值返回 ``None``。"""
    text = clean_value(value)
    if not text:
        return None
    text = re.sub(r"\s+", "", text)
    return text or None


def normalize_province(value: Any) -> Optional[str]:
    """省份统一成全称（``云南`` → ``云南省``）。"""
    text = clean_value(value)
    if not text:
        return None
    if text in PROVINCE_FULL:
        return text
    if text in PROVINCE_ALIASES:
        return PROVINCE_ALIASES[text]
    for full in sorted(PROVINCE_FULL, key=len, reverse=True):
        if full in text:
            return full
    for alias, full in sorted(PROVINCE_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if text.startswith(alias):
            return full
    return text


def normalize_city(value: Any, province: Optional[str] = None) -> Optional[str]:
    """城市保留原写法（含"市/州/县"后缀），仅去掉误带的省份前缀。"""
    text = clean_value(value)
    if not text:
        return None
    if text in MUNICIPALITIES:
        return text
    for full in sorted(PROVINCE_FULL, key=len, reverse=True):
        if text.startswith(full) and len(text) > len(full):
            text = text[len(full):]
            break
    if not text or text in PROVINCE_FULL:
        return None
    return text


def _header_key(value: Any) -> str:
    text = clean_value(value)
    if not text:
        return ""
    return _HEADER_SPACE_RE.sub("", text).lower()


_ALIAS_KEYS: Dict[str, str] = {_header_key(alias): field for alias, field in _HEADER_ALIASES.items()}


def _map_headers(values: Sequence[Any]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    for index, value in enumerate(values):
        field = _ALIAS_KEYS.get(_header_key(value))
        if field and field not in mapping:
            mapping[field] = index
    return mapping


def _looks_like_header(row: Mapping[str, Optional[str]]) -> bool:
    name = row.get("name")
    if not name:
        return True
    if _header_key(name) in {"name", "姓名"}:
        return True
    hits = sum(1 for value in row.values() if _header_key(value) in _ALIAS_KEYS)
    return hits >= 3


# --------------------------------------------------------------------------- #
# 表格读取
# --------------------------------------------------------------------------- #
def _row_dict(values: Sequence[Any], mapping: Mapping[str, int]) -> Dict[str, Optional[str]]:
    return {
        field: clean_value(values[index]) if index < len(values) else None
        for field, index in mapping.items()
    }


def _iter_xlsx(path: Path) -> Iterator[Tuple[int, Dict[str, Optional[str]]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的友好提示
        raise RuntimeError("读取 .xlsx 需要 openpyxl，请先执行 pip install openpyxl") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            buffer: List[Tuple[int, Sequence[Any]]] = []
            header_index: Optional[int] = None
            mapping: Dict[str, int] = {}
            rows = sheet.iter_rows(values_only=True)
            for row_number, values in enumerate(rows, 1):
                buffer.append((row_number, values))
                candidate = _map_headers(values)
                if "name" in candidate and len(candidate) >= 2:
                    header_index = len(buffer) - 1
                    mapping = candidate
                    break
                if len(buffer) >= 20:
                    break
            if header_index is None:
                continue

            for row_number, values in buffer[header_index + 1:]:
                row = _row_dict(values, mapping)
                if not _looks_like_header(row):
                    yield row_number, row
            for row_number, values in enumerate(rows, len(buffer) + 1):
                row = _row_dict(values, mapping)
                if not _looks_like_header(row):
                    yield row_number, row
            return
    finally:
        workbook.close()


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8/gbk", b"", 0, 1, f"无法解码文件：{path}")


def _iter_csv(path: Path) -> Iterator[Tuple[int, Dict[str, Optional[str]]]]:
    rows = list(csv.reader(io.StringIO(_read_text(path))))
    header_index: Optional[int] = None
    mapping: Dict[str, int] = {}
    for index, values in enumerate(rows[:20]):
        candidate = _map_headers(values)
        if "name" in candidate and len(candidate) >= 2:
            header_index = index
            mapping = candidate
            break
    if header_index is None:
        return
    for index, values in enumerate(rows[header_index + 1:], header_index + 2):
        row = _row_dict(values, mapping)
        if not _looks_like_header(row):
            yield index, row


def read_table_rows(path: Path) -> Iterator[Tuple[int, Dict[str, Optional[str]]]]:
    """按后缀分派读取 ``.xlsx`` / ``.csv``，返回 ``(行号, 字段字典)``。"""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return _iter_xlsx(path)
    if suffix in {".csv", ".txt"}:
        return _iter_csv(path)
    if suffix == ".xls":
        raise ValueError("不支持旧版 .xls，请另存为 .xlsx 或 .csv 后再导入")
    raise ValueError(f"不支持的文件类型：{path.suffix}（仅支持 .xlsx / .csv）")


# --------------------------------------------------------------------------- #
# 来源标识与记录构造
# --------------------------------------------------------------------------- #
def manual_image_key(path: Path) -> str:
    """手工来源的 ``images.image`` 主键，形如 ``manual:选调生去向汇总.xlsx``。"""
    resolved = Path(path).resolve()
    root = project_root()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        relative = resolved
    return f"{MANUAL_IMAGE_PREFIX}{relative.as_posix()}"


def _relative_posix(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(project_root()).as_posix()
    except ValueError:
        return resolved.as_posix()


def build_record(
    row: Mapping[str, Optional[str]],
    image: str,
    row_number: int,
    source_name: str,
) -> Optional[Dict[str, Any]]:
    """把一行表格数据转成 ``selects_records`` 记录；无姓名时返回 ``None``。"""
    name = normalize_name(row.get("name"))
    if not name:
        return None

    province = normalize_province(row.get("province"))
    city = normalize_city(row.get("city"), province)
    destination = clean_value(row.get("destination_org"))
    if not province or not city:
        text = " ".join(
            part for part in (destination, city, province, clean_value(row.get("position"))) if part
        )
        inferred_province, inferred_city = find_region(text)
        province = province or normalize_province(inferred_province)
        city = city or normalize_city(inferred_city, province)

    if province in MUNICIPALITIES and not city:
        city = province
    if city in MUNICIPALITIES and not province:
        province = city

    cohort, cohort_year = normalize_cohort_detail(clean_value(row.get("cohort")) or "")
    record: Dict[str, Any] = {
        "image": image,
        "block_index": 0,
        "name": name,
        "cohort": cohort,
        "cohort_year": cohort_year,
        "college": clean_value(row.get("college")),
        "major": clean_value(row.get("major")),
        "destination_org": destination,
        "city": city,
        "province": province,
        "position": clean_value(row.get("position")),
        "degree": clean_value(row.get("degree")),
        "confidence": 1.0,
        "note": clean_value(row.get("note")),
        "source_name": source_name,
        "source_row": row_number,
        "source_rows": [row_number],
    }
    refresh_record(record)
    return record


def _raw_text(record: Mapping[str, Any]) -> str:
    labels = (
        ("姓名", "name"),
        ("学院", "college"),
        ("专业", "major"),
        ("去向单位", "destination_org"),
        ("岗位", "position"),
        ("城市", "city"),
        ("省份", "province"),
        ("届别", "cohort"),
        ("学历", "degree"),
        ("备注", "note"),
    )
    return "；".join(
        f"{label}：{record[field]}" for label, field in labels if record.get(field)
    )


def refresh_record(record: Dict[str, Any]) -> None:
    """重算 ``field_count`` / ``raw_text`` / ``evidence``（合并重复行后调用）。"""
    record["field_count"] = sum(1 for field in CORE_FIELDS if record.get(field))
    record["raw_text"] = _raw_text(record)
    rows = record.get("source_rows") or [record.get("source_row")]
    rows = [int(row) for row in rows if row is not None]
    source_name = record.get("source_name") or "手工表格"
    record["evidence"] = f"手工整理自《{source_name}》第 {'、'.join(map(str, rows))} 行"


def build_image_row(path: Path, image: str, record_count: int) -> Dict[str, Any]:
    """构造来源行；手工来源没有原图，``local_path`` 指向表格本身。"""
    resolved = Path(path).resolve()
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return {
        "image": image,
        "stem": None,
        "notice_id": None,
        "notice_title": resolved.name,
        "organization": "手工录入",
        "release_time": None,
        "notice_type": "手工录入",
        "image_url": None,
        "alt": f"{resolved.name}（手工导入）",
        "sha256": digest,
        "size_bytes": resolved.stat().st_size,
        "local_path": _relative_posix(resolved),
        "ocr_engine": "manual",
        "ocr_line_count": record_count,
        "ocr_mean_score": None,
        "ocr_elapsed": None,
        "ocr_error": None,
    }


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #
def _norm_text(value: Any) -> str:
    return _PUNCTUATION_RE.sub("", str(value or "")).lower()


def _org_similar(left: Any, right: Any) -> bool:
    a, b = _norm_text(left), _norm_text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 3 and a in b:
        return True
    if len(b) >= 3 and b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.82


def _same_province(left: Any, right: Any) -> bool:
    a, b = normalize_province(left), normalize_province(right)
    return bool(a and b and a == b)


def _same_city(left: Any, right: Any) -> bool:
    a, b = normalize_city(left), normalize_city(right)
    return bool(a and b and a == b)


def _cohort_compatible(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    a, b = left.get("cohort_year"), right.get("cohort_year")
    if a is None or b is None:
        return True
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return True


def find_duplicate(
    record: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
) -> Tuple[Optional[Mapping[str, Any]], Optional[str]]:
    """在候选记录中找同一人；返回 ``(命中记录, 判定原因)``。"""
    name = normalize_name(record.get("name"))
    if not name:
        return None, None
    same_name = [
        candidate for candidate in candidates
        if normalize_name(candidate.get("name")) == name
    ]
    for candidate in same_name:
        if _org_similar(record.get("destination_org"), candidate.get("destination_org")):
            return candidate, "去向单位相同或高度相似"
    for candidate in same_name:
        if not _cohort_compatible(record, candidate):
            continue
        if record.get("destination_org") and candidate.get("destination_org"):
            if _same_province(record.get("province"), candidate.get("province")):
                return candidate, "同姓名且同省份"
            if _same_city(record.get("city"), candidate.get("city")):
                return candidate, "同姓名且同城市"
        elif not record.get("destination_org") and candidate.get("destination_org"):
            return candidate, "同姓名且原记录已有去向单位"
        elif _same_province(record.get("province"), candidate.get("province")):
            return candidate, "同姓名且同省份"
    return None, None


def _merge_record(target: Dict[str, Any], source: Mapping[str, Any]) -> None:
    for field in _MERGE_FIELDS:
        if not target.get(field) and source.get(field):
            target[field] = source[field]
    old_org = target.get("destination_org")
    new_org = source.get("destination_org")
    if old_org and new_org and len(str(new_org)) > len(str(old_org)):
        if _norm_text(old_org) in _norm_text(new_org):
            target["destination_org"] = new_org
    rows = list(target.get("source_rows") or [])
    for row in source.get("source_rows") or []:
        if row not in rows:
            rows.append(row)
    target["source_rows"] = rows
    refresh_record(target)


def _skip_info(
    record: Mapping[str, Any],
    duplicate: Mapping[str, Any],
    reason: str,
) -> Dict[str, Any]:
    return {
        "name": record.get("name"),
        "source_rows": record.get("source_rows") or [record.get("source_row")],
        "destination_org": record.get("destination_org"),
        "existing_id": duplicate.get("id"),
        "existing_image": duplicate.get("image"),
        "existing_destination_org": duplicate.get("destination_org"),
        "reason": reason,
    }


def _dedupe_records(
    records: Sequence[Dict[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    accepted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    merged = 0
    for record in records:
        duplicate, reason = find_duplicate(record, accepted)
        if duplicate is not None:
            _merge_record(duplicate, record)
            merged += 1
            db_duplicate, db_reason = find_duplicate(duplicate, candidates)
            if db_duplicate is not None:
                accepted.remove(duplicate)
                skipped.append(_skip_info(duplicate, db_duplicate, f"{db_reason}（合并后）"))
            continue
        db_duplicate, db_reason = find_duplicate(record, candidates)
        if db_duplicate is not None:
            skipped.append(_skip_info(record, db_duplicate, str(db_reason)))
            continue
        accepted.append(record)
    return accepted, skipped, merged


def _filter_records(
    records: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """只过滤、不合并：用于加载已经去重过的 sidecar。"""
    kept: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for record in records:
        duplicate, reason = find_duplicate(record, list(candidates) + kept)
        if duplicate is not None:
            skipped.append(_skip_info(record, duplicate, str(reason)))
            continue
        kept.append(dict(record))
    return kept, skipped


def _finalize_records(records: Sequence[Dict[str, Any]], source_name: str) -> List[Dict[str, Any]]:
    final: List[Dict[str, Any]] = []
    for index, record in enumerate(records):
        item = dict(record)
        item["source_name"] = item.get("source_name") or source_name
        item["block_index"] = index
        refresh_record(item)
        final.append(item)
    return final


def _db_candidates(conn: sqlite3.Connection, exclude_image: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, image, block_index, name, cohort, cohort_year, college, major,
               destination_org, city, province, position, degree
          FROM selects_records
         WHERE image != ?
        """,
        (exclude_image,),
    ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #
def import_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    manual_dir: Optional[Path] = None,
    keep_duplicates: bool = False,
    dry_run: bool = False,
    persist_sidecar: bool = True,
) -> Dict[str, Any]:
    """导入一个表格文件；``dry_run=True`` 时只返回报告，不写库。"""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    image = manual_image_key(path)
    rows = list(read_table_rows(path))
    records = [
        record for row_number, row in rows
        if (record := build_record(row, image, row_number, path.name)) is not None
    ]
    if not records:
        raise ValueError(f"未在 {path.name} 中找到有效数据行（至少需要姓名列）")

    candidates = _db_candidates(conn, exclude_image=image)
    if keep_duplicates:
        accepted: List[Dict[str, Any]] = list(records)
        skipped: List[Dict[str, Any]] = []
        merged = 0
    else:
        accepted, skipped, merged = _dedupe_records(records, candidates)
    accepted = _finalize_records(accepted, path.name)

    summary: Dict[str, Any] = {
        "file": _relative_posix(path),
        "image": image,
        "rows_read": len(rows),
        "rows_with_name": len(records),
        "imported": len(accepted),
        "skipped_duplicates": len(skipped),
        "merged_duplicates": merged,
        "dry_run": dry_run,
        "skipped": skipped,
    }
    if dry_run:
        summary["sample"] = accepted[:5]
        return summary

    image_row = build_image_row(path, image, len(accepted))
    upsert_image(conn, image_row)
    written = replace_records(conn, image, accepted)
    conn.commit()
    summary["records_written"] = written
    summary["fts_indexed"] = rebuild_fts(conn)
    summary["derived"] = apply_derived(conn)
    summary["total_records"] = int(
        conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0]
    )

    if persist_sidecar:
        target_dir = Path(manual_dir) if manual_dir else project_root() / "data" / "manual" / "imported"
        summary["sidecar"] = str(_write_sidecar(target_dir, path, image_row, accepted)).replace("\\", "/")

    _refresh_summary(conn, summary)
    return summary


def _write_sidecar(
    directory: Path,
    path: Path,
    image_row: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> Path:
    ensure_dir(directory)
    image = str(image_row["image"])
    sidecar = directory / f"{path.name}.json"
    if sidecar.exists():
        try:
            previous = json.loads(sidecar.read_text(encoding="utf-8"))
            previous_image = (previous.get("source") or {}).get("image")
        except (OSError, json.JSONDecodeError):
            previous_image = None
        if previous_image and previous_image != image:
            digest = hashlib.sha1(image.encode("utf-8")).hexdigest()[:8]
            sidecar = directory / f"{path.stem}-{digest}.json"
    payload = {
        "kind": "manual-source",
        "version": 1,
        "source": {
            **image_row,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        },
        "records": list(records),
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已保存手工来源快照：%s（%d 条）", sidecar, len(records))
    return sidecar


def _refresh_summary(conn: sqlite3.Connection, extra: Mapping[str, Any]) -> None:
    """把最新统计写回数据库同目录的 ``_db_summary.json``。"""
    database_rows = conn.execute("PRAGMA database_list").fetchall()
    main_file = next((row[2] for row in database_rows if row[1] == "main"), "")
    if not main_file:
        return
    db_path = Path(main_file)
    summary_path = db_path.parent / "_db_summary.json"
    existing: Dict[str, Any] = {}
    if summary_path.exists():
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update(stats(conn))
    existing["db_path"] = str(db_path).replace("\\", "/")
    existing["manual"] = {
        "file": extra.get("file"),
        "imported": extra.get("imported"),
        "skipped_duplicates": extra.get("skipped_duplicates"),
    }
    summary_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 重建时加载 sidecar
# --------------------------------------------------------------------------- #
def load_manual_sources(directory: Path) -> List[Tuple[Path, Dict[str, Any], List[Dict[str, Any]]]]:
    """读取 ``data/manual/imported`` 下全部来源快照。"""
    directory = Path(directory)
    if not directory.exists():
        return []
    sources: List[Tuple[Path, Dict[str, Any], List[Dict[str, Any]]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("跳过无法解析的手工来源快照 %s：%s", path.name, exc)
            continue
        if payload.get("kind") != "manual-source":
            continue
        source = payload.get("source") or {}
        if not source.get("image"):
            logger.warning("跳过缺少 image 的手工来源快照：%s", path.name)
            continue
        sources.append((path, source, list(payload.get("records") or [])))
    return sources


def sync_manual_sources(
    conn: sqlite3.Connection,
    directory: Path,
    *,
    prune: bool = True,
    keep_duplicates: bool = False,
) -> Dict[str, Any]:
    """把 sidecar 中的手工记录重新写入数据库（``build_database`` 调用）。"""
    directory = Path(directory)
    if not directory.exists():
        logger.info("未找到手工来源快照目录，跳过：%s", directory)
        return {
            "sources": 0,
            "records": 0,
            "written": 0,
            "skipped_duplicates": 0,
            "pruned_sources": 0,
        }
    sources = load_manual_sources(directory)
    total_records = 0
    total_skipped = 0
    total_written = 0
    total_pruned = 0
    source_images = set()

    for _path, source, records in sources:
        image = str(source["image"])
        source_images.add(image)
        conn.execute("DELETE FROM selects_records WHERE image = ?", (image,))
        candidates = _db_candidates(conn, exclude_image=image)
        if keep_duplicates:
            kept, skipped = [dict(record) for record in records], []
        else:
            kept, skipped = _filter_records(records, candidates)
        kept = _finalize_records(kept, str(source.get("notice_title") or image))
        upsert_image(conn, source)
        total_written += replace_records(conn, image, kept)
        total_records += len(records)
        total_skipped += len(skipped)

    if prune:
        prefix_length = len(MANUAL_IMAGE_PREFIX)
        rows = conn.execute(
            "SELECT image FROM images WHERE substr(image, 1, ?) = ?",
            (prefix_length, MANUAL_IMAGE_PREFIX),
        ).fetchall()
        for row in rows:
            image = str(row["image"])
            if image in source_images:
                continue
            conn.execute("DELETE FROM selects_records WHERE image = ?", (image,))
            conn.execute("DELETE FROM images WHERE image = ?", (image,))
            total_pruned += 1

    conn.commit()
    return {
        "sources": len(sources),
        "records": total_records,
        "written": total_written,
        "skipped_duplicates": total_skipped,
        "pruned_sources": total_pruned,
    }


__all__ = [
    "CORE_FIELDS",
    "MISSING_VALUES",
    "build_image_row",
    "build_record",
    "clean_value",
    "find_duplicate",
    "import_file",
    "is_manual_source",
    "load_manual_sources",
    "manual_image_key",
    "normalize_city",
    "normalize_name",
    "normalize_province",
    "read_table_rows",
    "refresh_record",
    "sync_manual_sources",
]
