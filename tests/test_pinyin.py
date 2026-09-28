"""拼音检索（``zhangsan`` → ``张三``）单元测试。"""
from __future__ import annotations

import pytest

from src.nlp import pinyin


def test_variants_full_and_initials():
    full, initials = pinyin.variants("张三")
    assert full == "zhangsan"
    assert initials == "zs"


def test_variants_empty_input():
    assert pinyin.variants("") == ("", "")


@pytest.mark.parametrize("query", ["zhangsan", "ZHANGSAN", "  zhangsan  ", "zhangs"])
def test_matches_full_pinyin(query: str):
    assert pinyin.matches("张三", query)


@pytest.mark.parametrize("query", ["zs", "z"])
def test_matches_initials(query: str):
    assert pinyin.matches("张三", query)


@pytest.mark.parametrize("query", ["lisi", "ls", "wangwu"])
def test_does_not_match_other_names(query: str):
    assert not pinyin.matches("张三", query)


def test_chinese_query_is_not_treated_as_pinyin():
    """汉字查询走字形匹配，不该在拼音层被判为命中。"""
    assert not pinyin.matches("张三", "张三")


def test_matches_rejects_empty_and_none():
    assert not pinyin.matches(None, "zhangsan")
    assert not pinyin.matches("张三", "")


def test_match_spans_returns_han_characters():
    """命中时必须回写汉字，否则用户看不出为什么这条记录会被搜出来。"""
    assert pinyin.match_spans("张三", "zhangsan") == ["张三"]
    assert pinyin.match_spans("张三", "lisi") == []


def test_expand_terms_replaces_pinyin_with_name():
    assert pinyin.expand_terms("张三", ["zhangsan", "选调"]) == ("张三",)


def test_variants_is_cached():
    assert pinyin.variants.cache_info().maxsize == 4096


def test_many_records_share_pinyin_prefix():
    """声母前缀只认 1~2 字母，避免短串误命中。"""
    assert pinyin.matches("张三", "zs")
    assert not pinyin.matches("张三", "zsk")


@pytest.mark.skipif(not pinyin.available(), reason="未安装 pypinyin")
def test_pypinyin_is_actually_installed():
    assert pinyin.variants("李四") == ("lisi", "ls")
