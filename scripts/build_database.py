"""结构化数据 → SQLite 数据库。

输入 ``data/processed/`` 下的两份产物：

* ``images.jsonl``            —— 图片/通知级元数据
* ``selects_records.jsonl``   —— 选调生去向记录（7 个核心字段）

输出 ``data/db/selects.sqlite``（含 FTS5 全文索引）。

用法::

    python scripts/build_database.py            # 增量写入（按 image 覆盖）
    python scripts/build_database.py --reset    # 清库重建
    python scripts/build_database.py --query 湖南 --limit 5   # 建完库顺手验一次检索
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.repo import (  # noqa: E402
    connect,
    default_db_path,
    facets,
    rebuild_fts,
    replace_records,
    search,
    stats,
    upsert_image,
)
from src.nlp.derive import apply_derived  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402
from src.utils.paths import data_dir  # noqa: E402

logger = get_logger("build_database")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("跳过无法解析的行（%s）：%s", path.name, exc)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="把 data/processed 的结构化数据写入 SQLite")
    parser.add_argument("--processed-dir", type=Path, default=None, help="默认 data/processed")
    parser.add_argument("--db", type=Path, default=None, help="数据库文件，默认 config/database.sqlite_path")
    parser.add_argument("--reset", action="store_true", help="清空所有表后重建")
    parser.add_argument("--query", type=str, default=None, help="建库后执行一次检索自检")
    parser.add_argument("--limit", type=int, default=5, help="自检检索返回条数")
    args = parser.parse_args()

    processed = args.processed_dir.resolve() if args.processed_dir else data_dir("processed")
    images_path = processed / "images.jsonl"
    records_path = processed / "selects_records.jsonl"

    if not records_path.exists():
        logger.error("未找到 %s，请先运行 scripts/extract_records.py", records_path)
        return 1

    images = read_jsonl(images_path)
    records = read_jsonl(records_path)

    db_path = args.db.resolve() if args.db else default_db_path()
    conn = connect(db_path)

    if args.reset:
        conn.execute("DELETE FROM selects_fts;")
        conn.execute("DELETE FROM selects_records;")
        conn.execute("DELETE FROM images;")
        conn.commit()
        logger.info("已清空数据库表，准备重建：%s", db_path)

    for row in images:
        upsert_image(conn, row)

    # 记录按图片分组，逐图覆盖写入（幂等，可反复重跑）
    by_image: Dict[str, List[Dict[str, Any]]] = {}
    for row in records:
        by_image.setdefault(str(row.get("image") or ""), []).append(row)

    written = 0
    for image, rows in by_image.items():
        if not image:
            continue
        rows.sort(key=lambda r: int(r.get("block_index") or 0))
        written += replace_records(conn, image, rows)
    conn.commit()

    indexed = rebuild_fts(conn)

    # 派生列（拼音、岗位类别、学历层次）可由原始字段重算，随入库一并回填
    derived = apply_derived(conn, force=args.reset)

    summary = stats(conn)
    summary["db_path"] = str(db_path).replace("\\", "/")
    summary["fts_indexed"] = indexed
    summary["images_input"] = len(images)
    summary["records_input"] = len(records)
    summary["records_written"] = written
    summary["derived"] = derived
    summary["facets"] = {k: len(v) for k, v in facets(conn).items()}
    (db_path.parent / "_db_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "入库完成：图片 %d，记录 %d（写入 %d），FTS 索引 %d，派生列回填 %d，数据库 %s",
        summary["images"], summary["records"], written, indexed,
        derived["updated"], db_path,
    )

    if args.query:
        result = search(conn, args.query, limit=args.limit)
        logger.info("检索自检 [%s]：命中 %d 条（模式 %s）", args.query, result["total"], result["mode"])
        for item in result["items"]:
            print(
                json.dumps(
                    {
                        k: item.get(k)
                        for k in ("id", "name", "name_pinyin", "cohort", "college", "major",
                                  "destination_org", "city", "province", "position_category",
                                  "degree_level")
                    },
                    ensure_ascii=False,
                )
            )

    conn.close()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
