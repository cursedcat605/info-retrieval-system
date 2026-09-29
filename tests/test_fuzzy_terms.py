"""模糊匹配词表（``config/fuzzy_terms.yaml`` + ``src/search/fuzzy.py``）的单元测试。

全部是纯函数 / 临时文件测试，不依赖数据库：把词表指向 ``tmp_path`` 下的
临时 YAML 即可，配合 :func:`src.search.fuzzy.reset_cache` 清掉 mtime 缓存。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search import fuzzy  # noqa: E402

#: 记下真实实现 —— autouse 夹具会把 ``fuzzy.fuzzy_path`` 换成临时路径，
#: 末尾的「真实词表自检」用例需要它来恢复
_REAL_FUZZY_PATH = fuzzy.fuzzy_path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """每个用例都用独立的临时词表，避免碰到真实配置与缓存。

    夹具**返回**临时词表路径（很多用例的返回值用不到，所以不强制声明）。
    """
    path = tmp_path / "fuzzy_terms.yaml"
    monkeypatch.setattr(fuzzy, "fuzzy_path", lambda: path)
    fuzzy.reset_cache()
    yield path
    fuzzy.reset_cache()


def write_terms(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("raw", "expected"), [
    ("信工", "信工"),
    ("  信 工 ", "信工"),
    ("XinGong", "xingong"),
    ("ＸＩＮ　ＧＯＮＧ", "xingong"),      # 全角 + 全角空格 → NFKC
    ("Zhang San", "zhangsan"),
    (None, ""),
    ("", ""),
])
def test_normalize(raw, expected):
    assert fuzzy.normalize(raw) == expected


def test_normalize_is_idempotent():
    once = fuzzy.normalize("  Zhang  Ｓａｎ ")
    assert fuzzy.normalize(once) == once


# --------------------------------------------------------------------------- #
# 解析：接受的几种写法
# --------------------------------------------------------------------------- #
def test_parse_groups_with_canonical_and_aliases():
    groups = fuzzy.parse_document({
        "groups": [{"canonical": "信息工程学院", "aliases": ["信工", "信工院"]}],
    })
    assert len(groups) == 1
    assert groups[0].canonical == "信息工程学院"
    assert groups[0].terms == ("信息工程学院", "信工", "信工院")


def test_parse_accepts_term_and_variants_aliases():
    groups = fuzzy.parse_document([{"term": "民族学与社会学学院", "variants": ["民社"]}])
    assert groups[0].terms == ("民族学与社会学学院", "民社")


def test_parse_accepts_list_of_lists():
    groups = fuzzy.parse_document([["信息工程学院", "信工", "信工院"]])
    assert groups[0].canonical == "信息工程学院"
    assert groups[0].terms == ("信息工程学院", "信工", "信工院")


def test_parse_accepts_alias_map_string_and_list_values():
    groups = fuzzy.parse_document({
        "aliases": {"信工": "信息工程学院", "新传": ["新闻与传播学院", "新闻与传播"]},
    })
    pairs = {(g.canonical, g.terms) for g in groups}
    assert ("信息工程学院", ("信息工程学院", "信工")) in pairs
    assert ("新闻与传播学院", ("新闻与传播学院", "新传")) in pairs
    assert ("新闻与传播", ("新闻与传播", "新传")) in pairs


def test_parse_merges_groups_and_aliases_in_one_document():
    groups = fuzzy.parse_document({
        "groups": [{"canonical": "信息工程学院", "aliases": ["信工"]}],
        "aliases": {"民社": "民族学与社会学学院"},
    })
    assert [g.canonical for g in groups] == ["信息工程学院", "民族学与社会学学院"]


def test_parse_deduplicates_identical_groups():
    groups = fuzzy.parse_document({
        "groups": [{"canonical": "信息工程学院", "aliases": ["信工"]}],
        "aliases": {"信工": "信息工程学院"},
    })
    assert len(groups) == 1


def test_parse_drops_alias_that_repeats_canonical():
    groups = fuzzy.parse_document([{"canonical": "信息工程学院", "aliases": ["信息工程学院"]}])
    assert groups == ()          # 去重后只剩主词 → 没有意义，忽略


# --------------------------------------------------------------------------- #
# 解析：坏输入的容忍
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("document", [
    None,
    {},
    {"groups": []},
    "一串字符串",           # 顶层类型不对
    123,
    {"groups": "不是列表"},
])
def test_parse_bad_shapes_yield_no_groups(document):
    assert fuzzy.parse_document(document) == ()


def test_parse_skips_rule_without_canonical():
    groups = fuzzy.parse_document([{"aliases": ["信工"]}, {"canonical": "信息工程学院", "aliases": ["信工"]}])
    assert [g.canonical for g in groups] == ["信息工程学院"]


def test_parse_skips_rule_without_alias():
    assert fuzzy.parse_document([{"canonical": "信息工程学院", "aliases": []}]) == ()


def test_parse_skips_unrecognized_entry_types():
    assert fuzzy.parse_document([42, None, "信工"]) == ()


def test_parse_truncates_overlong_group():
    aliases = [f"别名{i}" for i in range(fuzzy.MAX_TERMS_PER_GROUP + 5)]
    groups = fuzzy.parse_document([{"canonical": "信息工程学院", "aliases": aliases}])
    assert len(groups[0].terms) == fuzzy.MAX_TERMS_PER_GROUP
    assert groups[0].terms[0] == "信息工程学院"      # 主词一定保留


# --------------------------------------------------------------------------- #
# 加载：文件缺失 / 写坏 / mtime 热重载
# --------------------------------------------------------------------------- #
def test_missing_file_degrades_to_no_expansion(_isolate):
    assert fuzzy.load_groups() == ()
    assert fuzzy.available() is False
    assert fuzzy.expand("信工") == ["信工"]


def test_broken_yaml_does_not_raise(_isolate):
    write_terms(_isolate, "groups: [\n")
    assert fuzzy.load_groups() == ()
    assert fuzzy.expand("信工") == ["信工"]


def test_load_groups_reads_real_document(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工]}\n")
    assert [g.canonical for g in fuzzy.load_groups()] == ["信息工程学院"]
    assert fuzzy.available() is True


def test_cache_is_reused_until_signature_changes(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工]}\n")
    first = fuzzy.load_groups()
    assert fuzzy.load_groups() is first                   # 命中缓存，同一对象
    assert fuzzy.expand("信工") == ["信工", "信息工程学院"]

    # 内容变了 → 无需重启即可生效（这里连 mtime 也显式推后，避免同刻写入的偶然性）
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工, 信工院]}\n")
    os.utime(_isolate, (time.time() + 5, time.time() + 5))
    assert fuzzy.expand("信工") == ["信工", "信息工程学院", "信工院"]


def test_cache_invalidates_even_when_mtime_is_stale(_isolate):
    """mtime 没变但字节数变了（某些文件系统精度偏粗）也要重新加载。"""
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工]}\n")
    stamp = _isolate.stat().st_mtime_ns
    assert fuzzy.load_groups()
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工, 信工院]}\n")
    os.utime(_isolate, ns=(stamp, stamp))
    assert _isolate.stat().st_mtime_ns == stamp           # 确认 mtime 确实被压回原值
    assert "信工院" in fuzzy.expand("信工")


def test_reset_cache_forces_reload(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工]}\n")
    first = fuzzy.load_groups()
    assert first
    assert fuzzy.load_groups() is first        # 没动文件 → 直接用缓存

    fuzzy.reset_cache()
    again = fuzzy.load_groups()
    assert again is not first                  # 真的重新解析了一次
    assert [g.terms for g in again] == [g.terms for g in first]

    # 文件被删掉后，缓存里也不能再留着旧规则
    _isolate.unlink()
    fuzzy.reset_cache()
    assert fuzzy.load_groups() == ()


# --------------------------------------------------------------------------- #
# 扩展
# --------------------------------------------------------------------------- #
def test_expand_unknown_term_returns_itself(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工]}\n")
    assert fuzzy.expand("张三") == ["张三"]
    assert fuzzy.expand("") == []
    assert fuzzy.expand(None) == []


def test_expand_puts_user_term_first(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工, 信工院]}\n")
    assert fuzzy.expand("信息工程学院") == ["信息工程学院", "信工", "信工院"]
    assert fuzzy.expand("信工")[0] == "信工"


def test_expand_merges_groups_sharing_one_alias(_isolate):
    write_terms(_isolate, """
groups:
  - {canonical: 新闻与传播学院, aliases: [新传]}
  - {canonical: 新闻与传播, aliases: [新传]}
""")
    assert fuzzy.expand("新传") == ["新传", "新闻与传播学院", "新闻与传播"]


def test_expand_matches_alias_case_and_space_insensitively(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [XinGong]}\n")
    # 全角 + 全角空格写的别名，也应命中词表（返回时保留词表里的原写法）
    assert fuzzy.expand("ｘｉｎ　ｇｏｎｇ") == ["ｘｉｎ　ｇｏｎｇ", "信息工程学院", "XinGong"]
    assert fuzzy.expand("xingong")[1] == "信息工程学院"


def test_expand_accepts_canonical_spelled_with_spaces(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工]}\n")
    assert "信工" in fuzzy.expand(" 信息工程学院 ")


def test_expand_terms_reports_added_variants(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工, 信工院]}\n")
    expanded = fuzzy.expand_terms(["信工", "张三"])
    assert expanded[0] == {
        "term": "信工",
        "variants": ["信工", "信息工程学院", "信工院"],
        "added": ["信息工程学院", "信工院"],
    }
    # 没命中词表的词：variants 就是自己，added 为空（引擎据此判断「没必要走模糊分支」）
    assert expanded[1]["added"] == []


def test_expand_terms_keeps_order_and_length(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工]}\n")
    expanded = fuzzy.expand_terms(["张三", "信工", "李四"])
    assert [g["term"] for g in expanded] == ["张三", "信工", "李四"]


# --------------------------------------------------------------------------- #
# 供前端使用的视图
# --------------------------------------------------------------------------- #
def test_alias_pairs_lists_alias_to_canonical(_isolate):
    write_terms(_isolate, "groups:\n  - {canonical: 信息工程学院, aliases: [信工, 信工院]}\n")
    assert fuzzy.alias_pairs() == [
        {"alias": "信工", "canonical": "信息工程学院"},
        {"alias": "信工院", "canonical": "信息工程学院"},
    ]


def test_summary_shape(_isolate):
    write_terms(_isolate, """
groups:
  - {canonical: 信息工程学院, aliases: [信工, 信工院]}
  - {canonical: 民族学与社会学学院, aliases: [民社]}
""")
    info = fuzzy.summary()
    assert info["groups"] == 2
    assert info["aliases"] == 3
    assert info["available"] is True
    assert info["path"].endswith("fuzzy_terms.yaml")


def test_summary_reports_unavailable_when_missing():
    assert fuzzy.summary()["available"] is False


# --------------------------------------------------------------------------- #
# 真实词表自检：换机器 / 删文件后这里会立刻报出来
# --------------------------------------------------------------------------- #
def test_shipped_term_table_is_loadable(monkeypatch):
    monkeypatch.setattr(fuzzy, "fuzzy_path", _REAL_FUZZY_PATH)
    fuzzy.reset_cache()
    info = fuzzy.summary()
    assert info["available"] is True, "config/fuzzy_terms.yaml 缺失或不可解析"
    assert info["groups"] >= 1 and info["aliases"] >= 1


def test_shipped_table_expands_xingong_to_college(monkeypatch):
    monkeypatch.setattr(fuzzy, "fuzzy_path", _REAL_FUZZY_PATH)
    fuzzy.reset_cache()
    assert "信息工程学院" in fuzzy.expand("信工")
    # 词表里的主词不该被别名污染成「空扩展」
    assert fuzzy.expand("信息工程学院")[0] == "信息工程学院"
