"""检索层自检：一次跑通关键词 / 拼音 / 筛选 / 排序 / 分页 / 高亮 / 统计。

这不是单元测试（单元测试在 ``tests/``），而是**对着真实数据库的端到端冒烟脚本**：
改完检索逻辑后先跑它，能立刻看出哪一环坏了。

用法::

    python scripts/check_search.py
    python scripts/check_search.py --db data/db/selects.sqlite
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
    facet_counts,
    match_ids,
    stats_overview,
)
from src.search import get_detail, query, suggestions  # noqa: E402

PASS = "OK  "
FAIL = "FAIL"


class Checker:
    """极简断言收集器，避免为一个冒烟脚本引入测试框架。"""

    def __init__(self) -> None:
        self.results: List[Dict[str, Any]] = []
        self.failed = 0

    def check(self, name: str, condition: bool, detail: Any = None) -> None:
        ok = bool(condition)
        if not ok:
            self.failed += 1
        self.results.append({"name": name, "ok": ok, "detail": detail})
        print(f"[{PASS if ok else FAIL}] {name}" + (f"  → {detail}" if detail is not None else ""))

    def report(self) -> int:
        total = len(self.results)
        print(f"\n共 {total} 项，通过 {total - self.failed}，失败 {self.failed}")
        return 1 if self.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="检索层端到端自检")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db.resolve() if args.db else default_db_path()
    conn = connect(db_path)
    c = Checker()
    print(f"数据库：{db_path}\n")

    # ---------- 1. 关键词检索 ----------
    base = query(conn, "", page_size=5)
    c.check("无条件查询返回全部记录", base["total"] > 0, f"total={base['total']}")
    c.check("默认排序为届别新→旧", base["sort"] == "cohort_desc", base["sort"])

    years = [row["cohort_year"] for row in base["items"] if row["cohort_year"]]
    c.check("默认排序结果确实按届别降序", years == sorted(years, reverse=True), years)

    # ---------- 2. 拼音搜索 ----------
    sample = conn.execute(
        "SELECT name, name_pinyin, name_initials FROM selects_records "
        "WHERE COALESCE(name_pinyin,'') != '' LIMIT 1"
    ).fetchone()
    if sample:
        full, initials = sample["name_pinyin"], sample["name_initials"]
        by_full = query(conn, full, page_size=50)
        names = [row["name"] for row in by_full["items"]]
        c.check(f"全拼检索 {full} 命中 {sample['name']}", sample["name"] in names, names[:5])
        if initials:
            by_initials = query(conn, initials, page_size=50)
            c.check(
                f"声母缩写检索 {initials} 命中 {sample['name']}",
                sample["name"] in [r["name"] for r in by_initials["items"]],
            )
    else:
        c.check("数据库里存在带拼音的记录", False, "name_pinyin 全为空")

    # ---------- 3. 多条件筛选 ----------
    province_facet = base["facets"].get("province", [])
    c.check("省份分面带计数", bool(province_facet), province_facet[:3])
    if province_facet:
        top = province_facet[0]["value"]
        filtered = query(conn, "", filters={"province": [top]}, page_size=100)
        c.check(
            f"筛省份 {top} 后结果数等于分面计数",
            filtered["total"] == province_facet[0]["count"],
            f"{filtered['total']} vs {province_facet[0]['count']}",
        )
        # 同维度 OR
        if len(province_facet) > 1:
            second = province_facet[1]["value"]
            two = query(conn, "", filters={"province": [top, second]}, page_size=100)
            c.check(
                "同维度多选 = OR（数量相加）",
                two["total"] == province_facet[0]["count"] + province_facet[1]["count"],
                f"{two['total']} = {province_facet[0]['count']} + {province_facet[1]['count']}",
            )
        # 跨维度 AND
        deg_facet = [f for f in base["facets"].get("degree_level", []) if f["count"]]
        if deg_facet:
            deg = deg_facet[0]["value"]
            both = query(conn, "", filters={"province": [top], "degree_level": [deg]}, page_size=100)
            c.check(
                "跨维度 = AND（不超过任一侧）",
                0 < both["total"] <= min(province_facet[0]["count"], deg_facet[0]["count"]),
                f"total={both['total']}",
            )
            c.check(
                "已选条件生成可删除标签",
                any(t["dimension"] == "degree_level" for t in both["filter_tags"]),
                both["filter_tags"],
            )

        # 分面计数必须"排除自己"，让未选项仍显示真实数量
        sub = query(conn, "", filters={"province": [top]}, page_size=1)
        other = [f for f in sub["facets"].get("province", []) if f["value"] != top]
        c.check(
            "选定省份后，其它省份仍显示非零可选数量",
            bool(other) and other[0]["count"] > 0,
            other[:2],
        )

    # ---------- 4. 排序 ----------
    for sort_key in ("cohort_desc", "cohort_asc", "name_pinyin", "confidence_desc"):
        res = query(conn, "", sort=sort_key, page_size=8)
        c.check(f"排序 {sort_key} 可用", res["sort"] == sort_key and res["total"] > 0, res["sort"])
    named = query(conn, "", sort="name_pinyin", page_size=10)
    pinyins = [r["name_pinyin"] for r in named["items"] if r["name_pinyin"]]
    c.check("姓名拼音排序为升序", pinyins == sorted(pinyins), pinyins[:4])
    c.check("排序选项含中文名", all(o["label"] for o in named["sort_options"]))

    # ---------- 5. 分页 ----------
    p1 = query(conn, "", page=1, page_size=20)
    p2 = query(conn, "", page=2, page_size=20)
    c.check("每页 20 条", len(p1["items"]) <= 20, len(p1["items"]))
    c.check("总页数计算正确", p1["pages"] == -(-p1["total"] // 20), f"{p1['pages']} vs {p1['total']}")
    c.check("首页 has_prev=False / has_next 见后", p1["has_prev"] is False)
    if p1["pages"] > 1:
        ids1 = {r["id"] for r in p1["items"]}
        ids2 = {r["id"] for r in p2["items"]}
        c.check("第 2 页与第 1 页不重复", not (ids1 & ids2), f"重叠 {len(ids1 & ids2)}")

    # ---------- 6. 高亮 ----------
    hit = query(conn, base["items"][0]["name"] or "", page_size=5)
    if hit["items"]:
        hl = hit["items"][0]["highlight"]
        c.check("高亮包含全部字段的转义结果", "name" in hl["fields"], list(hl["fields"])[:4])
        c.check(
            "命中字段被 <mark> 包裹",
            bool(hl["matched_fields"]) and "<mark>" in hl["fields"].get(hl["matched_fields"][0], ""),
            hl["matched_fields"],
        )
    pinyin_hit = query(conn, sample["name_pinyin"] if sample else "zhang", page_size=3)
    if pinyin_hit["items"]:
        hl = pinyin_hit["items"][0]["highlight"]
        c.check(
            "拼音检索时高亮落在汉字上",
            sample is None or sample["name"] in hl["fields"]["name"],
            hl["fields"]["name"],
        )

    # ---------- 7. 多关键词 ----------
    if sample:
        inits = sample["name_initials"] or ""
        multi = query(conn, f"{sample['name']} 本科", page_size=10)
        c.check("多关键词返回分组统计", len(multi["groups"]) == 2, multi["groups"])
        c.check("多关键词模式可判定", multi["mode"] in ("and", "or"), multi["mode"])
        c.check("首词命中数 > 0", multi["groups"][0]["total"] > 0 or not inits)

    # ---------- 8. 详情 ----------
    rid = base["items"][0]["id"]
    detail = get_detail(conn, rid)
    c.check("详情页返回记录", detail is not None and detail["id"] == rid)
    if detail:
        c.check("详情页含原文与来源", "raw_text" in detail and "notice_title" in detail)
        c.check("详情页含字段中文名", bool(detail["field_labels"]))
    c.check("不存在的记录返回 None", get_detail(conn, -1) is None)

    # ---------- 9. 统计 ----------
    overview = stats_overview(conn)
    c.check("统计含省份分布", bool(overview["province"]), overview["summary"])
    c.check("统计含届别趋势（升序）", bool(overview["cohort"]),
            [r["value"] for r in overview["cohort"]])
    c.check("统计含岗位类别（饼图）", bool(overview["position_category"]),
            overview["position_category"])
    c.check("统计含学院分布", bool(overview["college"]), overview["college"][:3])
    c.check("统计含学历层次", bool(overview["degree_level"]), overview["degree_level"])

    # ---------- 10. 其它接口 ----------
    sug = suggestions(conn)
    c.check("自动补全覆盖省份/学院/专业", all(sug[k] for k in ("provinces", "colleges", "majors")))
    ids = match_ids(conn, "", limit=10)
    c.check("match_ids 可用于多词求交", len(ids) > 0, len(ids))
    fc = facet_counts(conn, "", dimensions=["province", "cohort_year"])
    c.check("facet_counts 可指定维度", set(fc) == {"province", "cohort_year"})

    conn.close()
    return c.report()


if __name__ == "__main__":
    raise SystemExit(main())
