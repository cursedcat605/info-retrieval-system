"""详情页的展示契约：字段 + 原图，不暴露 OCR 内部细节。

需求：进入某条记录详情时
- 不再展示「原文文本」整段 OCR 全文；
- 「来源信息」里不再有「图片文件」「OCR 引擎」两行（属于开发期信息）；
- 版式改为「左字段 / 右原图」，原图可直接点开。

这些断言锁的是「页面骨架」，防止以后重构又把这些内容加回来。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from web.backend.app import create_app

client = TestClient(create_app())

FRONTEND = Path(__file__).resolve().parents[1] / "web" / "frontend"


def test_detail_page_has_no_raw_text_block() -> None:
    html = (FRONTEND / "detail.html").read_text(encoding="utf-8")
    js = (FRONTEND / "detail.js").read_text(encoding="utf-8")
    css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    for source, name in ((html, "detail.html"), (js, "detail.js"), (css, "styles.css")):
        assert "rawtext" not in source, name
    assert "原文文本" not in html


def test_source_panel_drops_internal_fields() -> None:
    js = (FRONTEND / "detail.js").read_text(encoding="utf-8")
    assert "图片文件" not in js
    assert "OCR 引擎" not in js
    # 保留对用户有意义的来源字段
    for label in ("原文链接", "发布时间", "数据来源", "通知标题"):
        assert label in js, label


def test_detail_layout_is_facts_plus_image() -> None:
    html = (FRONTEND / "detail.html").read_text(encoding="utf-8")
    assert 'id="detail-main"' in html
    assert 'id="col-basic"' in html
    assert 'id="col-selects"' in html
    assert 'id="col-source"' in html
    assert 'id="image-thumb"' in html
    assert 'id="image-link"' in html


def test_detail_page_is_served() -> None:
    resp = client.get("/detail.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"
