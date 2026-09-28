"""回填派生检索列（拼音、姓名首字母、岗位类别、学历层次）。

派生列的存在意义：**让检索能力可以独立于 OCR 演进而升级**。
改了岗位关键词表或拼音方案后，只需要重跑本脚本，
不必重新走一遍 OCR + 字段抽取。

用法::

    python scripts/derive_fields.py              # 增量（只补空值行）
    python scripts/derive_fields.py --force      # 按当前规则全量重算
    python scripts/derive_fields.py --check      # 只统计当前填充情况，不写库
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.repo import connect, default_db_path  # noqa: E402
from src.database.schema import DERIVED_COLUMNS  # noqa: E402
from src.nlp.derive import apply_derived  # noqa: E402


def report(conn) -> dict:
    """各派生列的填充情况（含样本，便于人工确认规则是否合理）。"""
    total = conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0]
    out = {"total": int(total), "columns": {}}
    for name, _type in DERIVED_COLUMNS:
        filled = conn.execute(
            f"SELECT COUNT(*) FROM selects_records WHERE COALESCE({name}, '') != ''"
        ).fetchone()[0]
        samples = conn.execute(
            f"SELECT {name} AS value, COUNT(*) AS count FROM selects_records "
            f"WHERE COALESCE({name}, '') != '' GROUP BY {name} "
            f"ORDER BY count DESC LIMIT 8"
        ).fetchall()
        out["columns"][name] = {
            "filled": int(filled),
            "empty": int(total) - int(filled),
            "samples": [{"value": r["value"], "count": int(r["count"])} for r in samples],
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 selects_records 的派生检索列")
    parser.add_argument("--db", type=Path, default=None, help="默认 config/database.sqlite_path")
    parser.add_argument("--force", action="store_true", help="全量重算，而非只补空值行")
    parser.add_argument("--check", action="store_true", help="只输出填充情况，不写库")
    args = parser.parse_args()

    db_path = args.db.resolve() if args.db else default_db_path()
    conn = connect(db_path)

    if args.check:
        print(json.dumps(report(conn), ensure_ascii=False, indent=2))
        conn.close()
        return 0

    result = apply_derived(conn, force=args.force)
    result["db_path"] = str(db_path).replace("\\", "/")
    result["fill"] = {
        name: info["filled"] for name, info in report(conn)["columns"].items()
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
