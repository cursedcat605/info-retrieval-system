"""岗位类别 / 学历层次归类单元测试。

这两个函数是"踩坑后改出来"的，重点回归两件事：

1. **学院名绝不参与岗位判定** —— 否则"法学院""经济学院"会让几乎所有记录变成教育类；
2. **有岗位/单位但没命中具体规则时默认"公务/事业单位"** —— 选调生本就是公务员，
   回落到"其他"会让需求十五的饼图失真。
"""
from __future__ import annotations

import pytest

from src.nlp import position_category as pc


# --------------------------------------------------------------------------- #
# 回归：学院名不得影响判定
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("college", ["法学院", "经济学院", "民族学与社会学学院", "教育学院"])
def test_college_never_drives_classification(college: str):
    """即使学院名里带"学院/教育"，只要岗位与单位都是机关，就应判为公务/事业单位。"""
    assert pc.classify(destination_org="武汉市中级人民法院", college=college) == "公务/事业单位"


def test_law_school_record_is_not_education():
    """真实数据里最容易踩的坑：法学院 → 法院。"""
    assert pc.classify(
        position="审判辅助人员",
        destination_org="湖北省武汉市中级人民法院",
        college="法学院",
        major="法学",
    ) == "公务/事业单位"


# --------------------------------------------------------------------------- #
# 默认归类
# --------------------------------------------------------------------------- #
def test_org_only_defaults_to_civil_service():
    detail = pc.classify_detail(destination_org="某县某局")
    assert detail["category"] == "公务/事业单位"
    assert detail["basis"] == "默认"


def test_no_job_and_no_major_falls_back_to_other():
    assert pc.classify() == pc.OTHER
    assert pc.classify_detail()["basis"] is None


# --------------------------------------------------------------------------- #
# 关键机关后缀仍应命中公务/事业单位
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("org", [
    "中共山西省委组织部",
    "江苏省苏州市纪委",
    "成都市某街道办事处",
    "国家税务总局某市税务局",
    "某市人民政府办公室",
])
def test_government_orgs(org: str):
    """注意：财政局/税务局含"财政""税务"，会先命中金融类——这是有意的优先级设计。"""
    category = pc.classify(destination_org=org)
    assert category in ("公务/事业单位", "金融类")


def test_explicit_finance_wins_over_generic_government_rule():
    """"财政局" 同时含"财政"与"政府"，金融类排在前面，因此判金融类。"""
    assert pc.classify(destination_org="市财政局") == "金融类"


def test_explicit_tech_wins_over_generic_government_rule():
    assert pc.classify(position="软件开发工程师", destination_org="市政务服务中心") == "技术研发类"


def test_teacher_is_education():
    assert pc.classify(position="高中语文教师", destination_org="某中学") == "教育类"


def test_classify_detail_reports_basis():
    detail = pc.classify_detail(position="辅导员", destination_org="某大学")
    assert detail["category"] == "教育类"
    assert detail["basis"] == "岗位/单位"
    assert detail["evidence"]


# --------------------------------------------------------------------------- #
# 仅凭专业做弱推断
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("major", "expected"), [
    ("金融学", "金融类"),
    ("计算机科学与技术", "技术研发类"),
    ("教育学", "教育类"),
    ("民族学", pc.OTHER),
])
def test_weak_rules_use_major_only(major: str, expected: str):
    category = pc.classify(major=major)
    assert category == expected


def test_weak_rules_report_basis():
    assert pc.classify_detail(major="金融学")["basis"] == "专业"


def test_categories_and_order_are_canonical():
    assert pc.CATEGORIES == ("公务/事业单位", "技术研发类", "教育类", "金融类", "其他")
    assert pc.category_order(["其他", "金融类", "公务/事业单位"]) == (
        "公务/事业单位", "金融类", "其他")


# --------------------------------------------------------------------------- #
# 学历层次归一化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("degree", "expected"), [
    ("硕士研究生", "硕士"),
    ("硕士", "硕士"),
    ("研究生", "硕士"),
    ("MBA", "硕士"),
    ("本科", "本科"),
    ("大学本科", "本科"),
    ("学士", "本科"),
    ("博士研究生", "博士"),
    ("博士后", "博士"),
    ("PhD", "博士"),
])
def test_normalize_degree(degree: str, expected: str):
    assert pc.normalize_degree(degree) == expected


@pytest.mark.parametrize("degree", ["", None, "   ", "专科", "中专"])
def test_normalize_degree_unknown_returns_none(degree):
    """未识别时返回 None，绝不臆测。"""
    assert pc.normalize_degree(degree) is None


def test_degree_levels_are_the_filter_dimension():
    assert pc.DEGREE_LEVELS == ("本科", "硕士", "博士")
