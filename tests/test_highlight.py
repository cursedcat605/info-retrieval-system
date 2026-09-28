"""结果高亮单元测试（需求十二）。

两条硬性要求：

* 服务端**先转义再标记**，前端可以放心 ``innerHTML``；
* 拼音查询时高亮必须落在**汉字**上，否则用户看不到命中理由。
"""
from __future__ import annotations

import pytest

from src.search import highlight


@pytest.fixture()
def pattern():
    return highlight._compile(["选调"])


def test_mark_returns_escape_and_hit_flag(pattern):
    html, hit = highlight.mark("选调生经验分享", pattern)
    assert hit is True
    assert html == "<mark>选调</mark>生经验分享"


def test_mark_without_hit(pattern):
    html, hit = highlight.mark("普通通知", pattern)
    assert hit is False
    assert html == "普通通知"


def test_mark_escapes_html_before_marking(pattern):
    """字段里若含尖括号，必须先转义，不能原样输出成标签。"""
    html, _ = highlight.mark("<script>alert(1)</script>选调", pattern)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<mark>选调</mark>" in html


def test_mark_handles_empty_and_none():
    assert highlight.mark(None, None) == ("", False)
    assert highlight.mark("", None) == ("", False)


def test_compile_prefers_longer_term():
    """词典序：长词优先，避免"软件工程"被"软件"切断。"""
    pat = highlight._compile(["软件", "软件工程"])
    html, _ = highlight.mark("软件工程专业", pat)
    assert html == "<mark>软件工程</mark>专业"


def test_compile_is_case_insensitive():
    pat = highlight._compile(["zhangsan"])
    _, hit = highlight.mark("zhangsan", pat)
    assert hit is True
    _, hit_upper = highlight.mark("ZHANGSAN", pat)
    assert hit_upper is True


def test_effective_terms_maps_pinyin_to_han():
    record = {"name": "张三"}
    terms = highlight.effective_terms(record, ["zhangsan"])
    assert "张三" in terms
    assert "zhangsan" in terms


def test_build_highlights_han_for_pinyin_query():
    """核心断言：搜 zhangsan，姓名"张三"两个字要被标出来。"""
    record = {"name": "张三", "college": "经济学院", "raw_text": "张三 2024届 选调生"}
    result = highlight.build(record, ["zhangsan"])
    assert "<mark>张三</mark>" in result["fields"]["name"]
    assert "name" in result["matched_fields"]


def test_build_covers_all_declared_fields():
    result = highlight.build({"name": "张三"}, [])
    assert set(result["fields"]) == {key for key, _ in highlight.FIELD_LABELS}
    assert len(highlight.FIELD_LABELS) == 10


def test_build_extra_terms_are_highlighted():
    """多关键词检索里"命中任意词"的其它词也应参与高亮。"""
    record = {"name": "张三", "destination_org": "山西省某局"}
    result = highlight.build(record, ["张三"], extra_terms=["山西"])
    assert "<mark>山西</mark>" in result["fields"]["destination_org"]


def test_build_snippet_windows_around_hit():
    body = "前" * 300 + "选调生" + "后" * 300
    record = {"name": "张三", "raw_text": body}
    result = highlight.build(record, ["选调"])
    snippet = result["snippet"]
    assert snippet is not None
    assert "<mark>选调</mark>" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    # 窗口本身宽度 + 两个省略号 + <mark></mark> 标记
    assert len(snippet) <= highlight.SNIPPET_WIDTH + 20


def test_build_snippet_is_none_without_hit():
    record = {"name": "张三", "raw_text": "完全不相关的正文"}
    assert highlight.build(record, ["选调"])["snippet"] is None


def test_build_snippet_escapes_xss():
    body = "<img src=x onerror=alert(1)>选调"
    result = highlight.build({"name": "张三", "raw_text": body}, ["选调"])
    assert "<img" not in (result["snippet"] or "")
    assert "&lt;img" in (result["snippet"] or "")
