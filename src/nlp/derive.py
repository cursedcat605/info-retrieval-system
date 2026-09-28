"""派生字段回填：把可由原始字段重算的检索辅助列写回数据库。

``selects_records`` 里有两类列：

* **原始列**：由 OCR + 字段抽取产生（``name`` / ``position`` / ``degree`` …），
  是不可重算的事实，只能来自上游流水线；
* **派生列**：完全可由原始列推导（``name_pinyin`` / ``position_category`` …），
  唯一目的是**支撑检索与筛选**。

派生列一律通过本模块重建，好处是：词典或规则升级后，只要重跑一次
:func:`apply_derived` 就能让全部历史数据受益，不必重新走 OCR。

用法::

    from src.database import connect
    from src.nlp.derive import apply_derived

    conn = connect()
    print(apply_derived(conn))     # {"updated": 155, "skipped": 0}
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional, Tuple

from ..database.schema import DERIVED_COLUMNS, ensure_columns
from ..utils.logger import get_logger
from .pinyin import variants
from .position_category import classify, normalize_degree

logger = get_logger("derive")

#: 支持增量填写的派生列名
DERIVED_FIELD_NAMES: Tuple[str, ...] = tuple(name for name, _ in DERIVED_COLUMNS)


def build(name: Optional[str], position: Optional[str], degree: Optional[str],
          destination_org: Optional[str] = None, major: Optional[str] = None,
          college: Optional[str] = None) -> Dict[str, Optional[str]]:
    """由原始字段算出全部派生列的值。"""
    full, initials = variants(name or "")
    return {
        "name_pinyin": full or None,
        "name_initials": initials or None,
        "position_category": classify(position, destination_org, major, college),
        "degree_level": normalize_degree(degree),
    }


def apply_derived(conn: sqlite3.Connection, *, force: bool = False) -> Dict[str, Any]:
    """为 ``selects_records`` 回填派生列。

    :param force: 默认只补 ``name_pinyin`` 为空的行（增量、幂等）；
        ``True`` 时按当前规则重算全部行。
    :return: ``{"updated", "skipped", "total", "added_columns"}``
    """
    added = ensure_columns(conn)
    where = "" if force else "WHERE COALESCE(name_pinyin, '') = ''"
    rows = conn.execute(
        f"SELECT id, name, position, degree, destination_org, major, college "
        f"FROM selects_records {where}"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0]

    updated = 0
    for row in rows:
        values = build(
            row["name"], row["position"], row["degree"],
            row["destination_org"], row["major"], row["college"],
        )
        conn.execute(
            """
            UPDATE selects_records
               SET name_pinyin = ?, name_initials = ?,
                   position_category = ?, degree_level = ?
             WHERE id = ?
            """,
            (
                values["name_pinyin"], values["name_initials"],
                values["position_category"], values["degree_level"], row["id"],
            ),
        )
        updated += 1
    conn.commit()

    result = {
        "updated": updated,
        "skipped": int(total) - updated,
        "total": int(total),
        "added_columns": added,
    }
    if updated or added:
        logger.info(
            "派生字段已回填：更新 %d 条（新增列 %s）", updated, added or "无"
        )
    return result


def record_derived(name: Optional[str], position: Optional[str], degree: Optional[str],
                   destination_org: Optional[str] = None, major: Optional[str] = None,
                   college: Optional[str] = None) -> Dict[str, Optional[str]]:
    """供入库时逐条使用的别名（语义更贴近调用场景）。"""
    return build(name, position, degree, destination_org, major, college)


__all__ = ["DERIVED_FIELD_NAMES", "apply_derived", "build", "record_derived"]
