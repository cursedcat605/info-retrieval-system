"""把手整理好的 Excel/CSV 去向汇总表导入 SQLite。

典型用法::

    python scripts/import_records.py 选调生去向汇总.xlsx --dry-run
    python scripts/import_records.py 选调生去向汇总.xlsx
    python scripts/import_records.py data/manual/新表.xlsx --keep-duplicates

默认跳过与库中已有记录重复的行（同名 + 同单位/同省份/同届别）。导入成功后会：

1. 在 ``images`` 表登记一个 ``manual:<相对路径>`` 来源（没有原图）；
2. 覆盖写入该来源的全部记录（可反复重跑，幂等）；
3. 重建 FTS 索引并回填拼音/岗位类别/学历层次；
4. 把最终记录另存为 ``data/manual/imported/<文件名>.json``，
   供 ``scripts/build_database.py --reset`` 重建时恢复手工数据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.manual import import_file  # noqa: E402
from src.database.repo import connect, default_db_path  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402
from src.utils.paths import data_dir  # noqa: E402

logger = get_logger("import_records")


def main() -> int:
    parser = argparse.ArgumentParser(description="把手整理的去向汇总表导入 SQLite")
    parser.add_argument("file", type=Path, help="待导入的 .xlsx 或 .csv")
    parser.add_argument("--db", type=Path, default=None, help="数据库文件，默认 config/database.sqlite_path")
    parser.add_argument("--manual-dir", type=Path, default=None, help="来源快照目录，默认 data/manual/imported")
    parser.add_argument("--dry-run", action="store_true", help="只解析并输出报告，不写数据库")
    parser.add_argument("--keep-duplicates", action="store_true", help="保留与现有记录重复的行")
    parser.add_argument("--no-sidecar", action="store_true", help="不保存来源快照（不推荐）")
    args = parser.parse_args()

    source = args.file.resolve()
    if not source.exists():
        logger.error("文件不存在：%s", source)
        return 1

    db_path = args.db.resolve() if args.db else default_db_path()
    manual_dir = args.manual_dir.resolve() if args.manual_dir else data_dir("manual", "imported")
    conn = connect(db_path)
    try:
        summary = import_file(
            conn,
            source,
            manual_dir=manual_dir,
            keep_duplicates=args.keep_duplicates,
            dry_run=args.dry_run,
            persist_sidecar=not args.no_sidecar,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        logger.error("导入失败：%s", exc)
        return 1
    finally:
        conn.close()

    if args.dry_run:
        sample = summary.pop("sample", [])
        logger.info(
            "试运行：读取 %d 行，可导入 %d 条，跳过重复 %d 条，文件内合并 %d 条",
            summary["rows_read"], summary["imported"],
            summary["skipped_duplicates"], summary["merged_duplicates"],
        )
        for item in sample:
            print(json.dumps(
                {key: item.get(key) for key in (
                    "name", "cohort", "college", "major", "destination_org",
                    "city", "province", "position", "degree",
                )},
                ensure_ascii=False,
            ))
        if summary.get("skipped"):
            print("--- 跳过的重复行 ---")
            for item in summary["skipped"]:
                print(json.dumps(item, ensure_ascii=False))
    else:
        logger.info(
            "导入完成：写入 %d 条，跳过重复 %d 条，当前总记录 %d 条",
            summary["records_written"], summary["skipped_duplicates"],
            summary["total_records"],
        )
        for item in summary.get("skipped", []):
            logger.warning(
                "跳过重复：%s（第 %s 行）→ 已存在记录 #%s %s",
                item.get("name"), item.get("source_rows"),
                item.get("existing_id"), item.get("existing_destination_org"),
            )

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
