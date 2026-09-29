"""学院字段抽取的回归契约。

海报正文里"学院"经常紧跟着"届别+身份"，而抽取时是把同一段发言的所有格子
**直接拼成一串**（``"".join(cells)``），于是正则的 ``{2,10}`` 前缀会贪心地把
标题文字一起吃掉，产出 ``届广东定向选调生文学院`` 这种脏值，最终污染
侧边栏「学院」维度。

这里锁住两件事：

1. **粘连前缀必须剥掉**，不能用"更长的匹配"取胜；
2. **OCR 错字要对齐白名单**（``民族学与在会学学院`` → 民族学与社会学学院），
   但命中不唯一时宁可不猜 —— 写错一个学院比留个可疑值更糟。
"""
from __future__ import annotations

import pytest

from src.nlp.field_extractor import find_college, find_major


# --------------------------------------------------------------------------- #
# 回归：届别 / 身份被粘进学院名
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("李员员2021届选调生小国语学院翻译专业", "外国语学院"),
        ("朱泽2020届江苏选调生民族学与在会学学院社会学专业", "民族学与社会学学院"),
        ("2022届选调生信息与工程学院2020级现代教育技术专业硕士研究生", "信息工程学院"),
        ("2023届定向选调生文学院2020级汉语言文字学专业硕士生", "文学院"),
        ("2020届湖北集中选调生文学院2016级汉语国际教育专业", "文学院"),
        ("2024届福建选调生文学院2020级汉语言文学专业本科生", "文学院"),
        ("2021届宁夏选调生中国少数民族语言文学学院2021届翻译专业",
         "中国少数民族语言文学学院"),
        ("民族学与社会学学院", "民族学与社会学学院"),
    ],
)
def test_college_drops_glued_cohort_prefix(text: str, expected: str):
    value, _span = find_college(text)
    assert value == expected


@pytest.mark.parametrize(
    "text",
    [
        "李员员2021届选调生小国语学院翻译专业",
        "朱泽2020届江苏选调生民族学与在会学学院社会学专业",
        "2023届定向选调生文学院2020级汉语言文字学专业硕士生",
    ],
)
def test_college_never_contains_cohort_noise(text: str):
    """任何学院值都不得含"选调生"或以"届/级"开头（脏值的表面特征）。"""
    value, _span = find_college(text)
    assert value is not None
    assert "选调生" not in value
    assert not value.startswith(("届", "级", "年"))


def test_college_span_points_at_the_real_name():
    """剥掉前缀后区间要右移，否则专业抽取仍会踩到标题文字。"""
    text = "2023届定向选调生文学院2020级汉语言文字学专业硕士生"
    value, span = find_college(text)
    assert value == "文学院"
    assert span is not None
    assert text[span[0]:span[1]] == "文学院"


def test_college_absent_returns_none():
    assert find_college("无学院字样的普通文本") == (None, None)


def test_major_still_extracted_when_college_prefixed():
    """学院区间修正后，专业仍要在学院右侧正常命中。"""
    text = "李员员2021届选调生小国语学院翻译专业"
    college, span = find_college(text)
    assert college == "外国语学院"
    assert find_major(text, span) == "翻译"


# --------------------------------------------------------------------------- #
# 数据层面的兜底：库里不允许出现带届别噪声的学院值
# --------------------------------------------------------------------------- #
def test_no_dirty_college_value_in_database(conn):
    rows = conn.execute(
        "SELECT DISTINCT college FROM selects_records WHERE college IS NOT NULL"
    ).fetchall()
    assert rows, "学院字段不应全为空"
    dirty = [
        row["college"] for row in rows
        if "选调生" in row["college"] or row["college"][:1] in {"届", "级", "年"}
    ]
    assert dirty == []
