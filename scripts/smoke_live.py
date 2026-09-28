# -*- coding: utf-8 -*-
"""对运行中的服务做真实 HTTP 冒烟验证（避免 PowerShell 管道破坏中文）。

拼音查询词默认用占位名 ``zhangsan``，以便本文件可安全公开。
如需校验真实数据，本地执行前设环境变量，例如：

    $env:SMOKE_PINYIN_QUERY = "<某位真实姓名全拼>"
    python scripts/smoke_live.py
"""
import os

import httpx

B = "http://127.0.0.1:8000"
PINYIN_QUERY = os.getenv("SMOKE_PINYIN_QUERY", "zhangsan")


def main() -> None:
    r = httpx.get(f"{B}/api/search", params={"q": PINYIN_QUERY}, timeout=20)
    j = r.json()
    hit = [i["name"] for i in j["items"]]
    print(f"1 拼音检索 {PINYIN_QUERY} ->", r.status_code, "total=", j["total"], "命中", hit)

    r = httpx.get(f"{B}/api/search",
                  params=[("province", "重庆市"), ("degree_level", "硕士")], timeout=20)
    j = r.json()
    print("2 跨维度 AND（重庆市 ∧ 硕士）->", r.status_code, "total=", j["total"],
          "标签", [t["display"] for t in j["filter_tags"]])

    r = httpx.get(f"{B}/api/search", params={"province": "重庆市", "page_size": 1}, timeout=20)
    rid = r.json()["items"][0]["id"]
    d = httpx.get(f"{B}/api/record/{rid}", timeout=20).json()
    print("3 详情页 ->", d["name"], d["college"], "| 原文长度", len(d.get("source_text") or ""),
          "| 链接", (d.get("source_url") or "")[:60])

    r = httpx.get(f"{B}/api/stats", timeout=20).json()
    print("4 统计 ->", r["summary"], "| 省份前三",
          [(x["value"], x["count"]) for x in r["province"][:3]])

    r = httpx.get(f"{B}/", timeout=20)
    print("5 首页 ->", r.status_code, "含标题", "选调生去向信息检索系统" in r.text)

    r = httpx.get(f"{B}/api/search", params={"college": "法学院"}, timeout=20).json()
    print("6 学院筛选 法学院 -> total=", r["total"])


if __name__ == "__main__":
    main()
