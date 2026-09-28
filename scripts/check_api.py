"""Web 接口端到端自检（需求十 ~ 十五）。

用 FastAPI 的 ``TestClient`` 直接打接口，无需先起服务：

    python scripts/check_api.py

覆盖：检索/拼音/多条件筛选（OR·AND）/排序/分页/高亮/详情/统计/补全/静态资源。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from web.backend.app import create_app  # noqa: E402


class Checker:
    def __init__(self) -> None:
        self.total = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            print(f"[OK  ] {name}" + (f"  → {detail}" if detail else ""))
        else:
            self.failed += 1
            print(f"[FAIL] {name}" + (f"  → {detail}" if detail else ""))

    def report(self) -> int:
        print(f"\n共 {self.total} 项，通过 {self.total - self.failed}，失败 {self.failed}")
        return 1 if self.failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    argparse.ArgumentParser(description="Web 接口自检").parse_args(argv)
    c = Checker()
    client = TestClient(create_app())

    # ---------------- 1. 前端字典与体检 ---------------- #
    cfg = client.get("/api/config").json()
    c.check("GET /api/config 返回 7 个筛选维度", len(cfg.get("dimensions", [])) == 7,
            str([d["key"] for d in cfg.get("dimensions", [])]))
    c.check("GET /api/config 返回 5 种排序", len(cfg.get("sorts", [])) == 5)
    c.check("默认每页 20 条", cfg.get("default_page_size") == 20, str(cfg.get("default_page_size")))

    health = client.get("/api/health").json()
    c.check("GET /api/health 正常且库中有数据",
            health.get("status") == "ok" and health["stats"]["records"] > 0,
            f"records={health['stats']['records']} images={health['stats']['images']}")

    # ---------------- 2. 无条件检索 ---------------- #
    base = client.get("/api/search").json()
    c.check("无条件检索返回全部记录", base["total"] == health["stats"]["records"],
            f"total={base['total']}")
    c.check("默认排序为届别（新→旧）", base["sort"] == "cohort_desc", base["sort"])
    c.check("默认每页 20 条且不超过", 0 < len(base["items"]) <= 20, f"items={len(base['items'])}")
    c.check("分面含 7 个维度且带计数",
            len(base["facets"]) == 7 and all(
                isinstance(v, list) for v in base["facets"].values()),
            str({k: len(v) for k, v in base["facets"].items()}))
    c.check("分面选项带 label / selected 字段",
            all("label" in o and "selected" in o
                for opts in base["facets"].values() for o in opts))
    c.check("排序项带中文标签",
            all(o.get("label") for o in base["sort_options"]))

    years = [i["cohort_year"] for i in base["items"] if i["cohort_year"]]
    c.check("届别确实按新→旧排列", years == sorted(years, reverse=True), str(years))

    # ---------------- 3. 拼音检索（需求：拼音搜索） ---------------- #
    sample = next((i for i in base["items"] if i.get("name_pinyin")), None)
    if sample:
        py = sample["name_pinyin"]
        r1 = client.get("/api/search", params={"q": py}).json()
        c.check(f"全拼 {py} 命中「{sample['name']}」",
                any(i["id"] == sample["id"] for i in r1["items"]), f"total={r1['total']}")
        ini = sample.get("name_initials") or ""
        if ini:
            r2 = client.get("/api/search", params={"q": ini}).json()
            c.check(f"声母缩写 {ini} 命中「{sample['name']}」",
                    any(i["id"] == sample["id"] for i in r2["items"]), f"total={r2['total']}")
        hit = next((i for i in r1["items"] if i["id"] == sample["id"]), None)
        c.check("拼音命中时高亮作用在汉字上（而非拼音）",
                bool(hit) and "<mark>" in hit["highlight"]["fields"]["name"],
                (hit or {}).get("highlight", {}).get("fields", {}).get("name", ""))
    else:
        c.check("存在可用的 name_pinyin 样本", False, "库中无拼音数据")

    # ---------------- 4. 多条件筛选（需求十） ---------------- #
    prov_opts = base["facets"]["province"]
    top = prov_opts[0]["value"]
    one = client.get("/api/search", params={"province": top}).json()
    c.check(f"单省份筛选 {top} 全部命中该省",
            one["total"] > 0 and all(i["province"] == top for i in one["items"]),
            f"total={one['total']}")

    if len(prov_opts) > 1:
        second = prov_opts[1]["value"]
        cnt_a = prov_opts[0]["count"]
        cnt_b = prov_opts[1]["count"]
        both = client.get("/api/search", params=[("province", top), ("province", second)]).json()
        c.check("同维度多选 = OR（计数相加）",
                both["total"] == cnt_a + cnt_b,
                f"{top}({cnt_a}) + {second}({cnt_b}) = {both['total']}")
        c.check("同维度多选后标签生成 2 个",
                len([t for t in both["filter_tags"] if t["dimension"] == "province"]) == 2)

    # 分面计数排除自身维度：只看省份时，其他省份的计数不应被清零
    one_counts = {o["value"]: o["count"] for o in one["facets"]["province"]}
    global_counts = {o["value"]: o["count"] for o in base["facets"]["province"]}
    c.check("分面计数排除自身维度（其他省份计数保持原值）",
            one_counts == global_counts and all(v > 0 for v in one_counts.values()),
            f"{top}={one_counts.get(top)} / 全局 {global_counts.get(top)}")

    deg = base["facets"]["degree_level"]
    if deg:
        d = deg[0]["value"]
        cross = client.get("/api/search", params=[("province", top), ("degree_level", d)]).json()
        c.check(f"跨维度 = AND（{top} ∧ {d}）",
                cross["total"] > 0 and all(
                    i["province"] == top and i["degree_level"] == d for i in cross["items"]),
                f"total={cross['total']}")
        c.check("跨维度结果集 ⊆ 单维度结果集",
                cross["total"] <= one["total"],
                f"{cross['total']} <= {one['total']}")
        c.check("已选条件标签含两个不同维度",
                len({t["dimension"] for t in cross["filter_tags"]}) == 2,
                str([t["display"] for t in cross["filter_tags"]]))

    # ---------------- 5. 排序（需求 11.2） ---------------- #
    byname = client.get("/api/search", params={"sort": "name_pinyin", "page_size": 50}).json()
    names = [i["name_pinyin"] for i in byname["items"] if i["name_pinyin"]]
    c.check("按姓名拼音排序为升序", names == sorted(names), str(names[:5]))
    for sort_key in ("relevance", "cohort_desc", "cohort_asc", "name_pinyin", "confidence_desc"):
        r = client.get("/api/search", params={"sort": sort_key}).json()
        c.check(f"排序 {sort_key} 可用", r["sort"] == sort_key and "items" in r)

    # ---------------- 6. 分页（需求 11.3） ---------------- #
    p1 = client.get("/api/search", params={"page": 1, "page_size": 20}).json()
    p2 = client.get("/api/search", params={"page": 2, "page_size": 20}).json()
    c.check("页数 = ceil(总数/每页)",
            p1["pages"] == -(-p1["total"] // 20), f"{p1['total']}/20 → {p1['pages']}")
    c.check("第 1 页 has_prev=False", p1["has_prev"] is False)
    c.check("第 2 页 has_next 正确", p2["has_next"] == (2 < p2["pages"]))
    c.check("相邻页结果不重叠",
            not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}))
    c.check("page_size 超过上限被夹紧",
            client.get("/api/search", params={"page_size": 9999}).json()["page_size"]
            == cfg["max_page_size"])

    # ---------------- 7. 高亮（需求十二） ---------------- #
    kw = None
    for cand in (top,):
        if cand:
            kw = cand
    hl = client.get("/api/search", params={"q": kw}).json()
    item = hl["items"][0] if hl["items"] else base["items"][0]
    c.check("每条结果都带 highlight.fields",
            all("fields" in i["highlight"] for i in hl["items"]))
    c.check("高亮字段覆盖姓名/学院/专业/省份/单位/岗位/标题",
            {"name", "college", "major", "province", "destination_org",
             "position", "notice_title"} <= set(item["highlight"]["fields"]))
    c.check("命中字段被 <mark> 包裹（可作为命中理由）",
            any("<mark>" in v for v in item["highlight"]["fields"].values()),
            str([k for k, v in item["highlight"]["fields"].items() if "<mark>" in v]))
    c.check("高亮内容已转义（无裸 <script>）",
            all("<script" not in v for i in hl["items"] for v in i["highlight"]["fields"].values()))

    # ---------------- 8. 多关键词（AND / OR） ---------------- #
    multi = client.get("/api/search", params={"q": f"{kw} 选调"}).json()
    c.check("多关键词返回 groups 分组信息", len(multi.get("groups", [])) == 2,
            str(multi.get("mode")))
    c.check("多关键词 mode 为 and 或 or", multi["mode"] in ("and", "or"), multi["mode"])

    # ---------------- 9. 详情（需求十四） ---------------- #
    rid = base["items"][0]["id"]
    detail = client.get(f"/api/record/{rid}")
    c.check("GET /api/record/{id} 返回 200", detail.status_code == 200, str(detail.status_code))
    d = detail.json()
    c.check("详情含完整结构化字段",
            all(k in d for k in ("name", "college", "major", "degree", "cohort_year",
                                 "province", "city", "destination_org", "position")),
            str(sorted(k for k in d if k in ("name", "college", "major"))))
    c.check("详情含来源信息（原文链接/发布时间/来源单位）",
            bool(d.get("source_url")) and "release_time" in d and "organization" in d,
            str(d.get("release_time")))
    c.check("详情含原文证据与字段中文名映射",
            "evidence" in d and len(d.get("field_labels", [])) >= 8,
            f"field_labels={len(d.get('field_labels', []))}")
    c.check("详情含原文文本（source_text 或 raw_text）",
            bool(d.get("source_text") or d.get("raw_text")))
    c.check("详情含原图地址",
            bool(d.get("image_local_url")), str(d.get("image_local_url")))
    c.check("不存在的记录返回 404",
            client.get("/api/record/99999999").status_code == 404)
    c.check("列表项已裁剪 raw_text / evidence（减小体积）",
            all("raw_text" not in i and "evidence" not in i for i in base["items"]))
    c.check("列表项带 image_local_url 与 source_url",
            all(i.get("image_local_url") and i.get("source_url") for i in base["items"]))

    # ---------------- 10. 统计（需求十五） ---------------- #
    st = client.get("/api/stats").json()
    c.check("统计含概览 summary",
            all(k in st["summary"] for k in ("records", "images", "provinces", "colleges", "cities")),
            str(st["summary"]))
    c.check("15.1 省份分布可用于柱状图", bool(st["province"]) and "count" in st["province"][0],
            str(st["province"][0]))
    cy = [r["value"] for r in st["cohort"]]
    c.check("15.2 届别趋势按年份升序", cy == sorted(cy), str(cy))
    c.check("15.3 岗位类别（饼图）已归一化",
            bool(st["position_category"]) and any(
                r["value"] in ("公务/事业单位", "技术研发类", "教育类", "金融类", "其他")
                for r in st["position_category"]),
            str(st["position_category"]))
    c.check("15.4 学院分布可用于横向柱状图", bool(st["college"]) and len(st["college"]) <= 15,
            str(st["college"][:2]))
    c.check("附加：学历层次与字段填充率",
            bool(st.get("degree_level")) and bool(st.get("fill_rate")),
            str(st.get("degree_level")))

    # ---------------- 11. 补全 / 分面独立接口 ---------------- #
    sug = client.get("/api/suggest").json()
    c.check("/api/suggest 覆盖省份/学院/专业",
            all(sug.get(k) for k in ("provinces", "colleges", "majors")),
            f"provinces={len(sug.get('provinces', []))} colleges={len(sug.get('colleges', []))}")
    fc = client.get("/api/facets", params={"q": top}).json()
    c.check("/api/facets 支持关键词过滤", "facets" in fc and fc["facets"]["province"])

    # ---------------- 12. 静态资源 ---------------- #
    c.check("GET / 返回前端页面", client.get("/").status_code == 200 and
            "选调生" in client.get("/").text, "index.html")
    c.check("GET /detail.html 可用", client.get("/detail.html").status_code == 200)
    c.check("GET /stats.html 可用", client.get("/stats.html").status_code == 200)
    c.check("GET /styles.css 可用", client.get("/styles.css").status_code == 200)
    c.check("GET /app.js 可用", client.get("/app.js").status_code == 200)
    img = base["items"][0].get("image")
    if img:
        resp = client.get(f"/images/{quote(img)}")
        c.check("GET /images/<图片名> 可访问原图", resp.status_code == 200,
                f"{resp.status_code} {len(resp.content)} bytes")

    return c.report()


if __name__ == "__main__":
    raise SystemExit(main())
