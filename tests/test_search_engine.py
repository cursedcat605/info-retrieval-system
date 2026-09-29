"""检索层集成测试（需求十 ~ 十四），基于线上那份只读数据库。"""
from __future__ import annotations

import math

import pytest

from src.search import engine
from src.search import highlight as highlight_util
from src.utils.text import shorten_region


# --------------------------------------------------------------------------- #
# 纯函数：查询解析与筛选归一化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("raw", "expected"), [
    ("张三 选调", ["张三", "选调"]),
    ("张三,选调、山西", ["张三", "选调", "山西"]),
    ("  张三   张三  ", ["张三"]),
    ("", []),
    ("  ", []),
])
def test_parse_query(raw: str, expected):
    assert engine.parse_query(raw) == expected


def test_parse_filters_normalizes_shapes():
    assert engine.parse_filters({"province": "湖北省"}) == {"province": ["湖北省"]}
    assert engine.parse_filters({"province": ["湖北省", "江苏省"]}) == {
        "province": ["湖北省", "江苏省"]}
    assert engine.parse_filters({"province": ["湖北省", "湖北省"]}) == {
        "province": ["湖北省"]}


def test_parse_filters_drops_unknown_dimension_and_empty():
    assert engine.parse_filters({"unknown": "x", "province": ""}) == {}
    assert engine.parse_filters(None) == {}


def test_sort_labels_are_chinese():
    assert engine.SORT_LABELS == {
        "relevance": "相关度",
        "cohort_desc": "届别（新→旧）",
        "cohort_asc": "届别（旧→新）",
        "name_pinyin": "姓名拼音",
        "confidence_desc": "置信度",
    }


def test_dimension_labels_cover_seven_dimensions():
    assert list(engine.DIMENSION_LABELS) == [
        "province", "position_category", "degree_level",
        "cohort_year", "college", "major", "city",
    ]


def test_page_size_defaults_and_limits():
    assert engine.DEFAULT_PAGE_SIZE == 20
    assert engine.MAX_PAGE_SIZE == 100
    assert engine.DEFAULT_PAGE_SIZE <= engine.MAX_PAGE_SIZE


# --------------------------------------------------------------------------- #
# 检索：无查询词
# --------------------------------------------------------------------------- #
def test_empty_query_returns_everything(conn, record_count):
    result = engine.query(conn, "")
    assert result["total"] == record_count
    assert result["mode"] == "all"
    assert result["sort"] == "cohort_desc"          # 需求 11.1
    assert len(result["items"]) == engine.DEFAULT_PAGE_SIZE


def test_default_sort_is_newest_first(conn):
    years = [i["cohort_year"] for i in engine.query(conn, "")["items"] if i["cohort_year"]]
    assert years == sorted(years, reverse=True)


def test_facets_present_for_all_dimensions(conn):
    result = engine.query(conn, "")
    assert set(result["facets"]) == set(engine.DIMENSION_LABELS)
    for options in result["facets"].values():
        for option in options:
            assert option["count"] > 0
            assert option["label"] and option["selected"] is False


# --------------------------------------------------------------------------- #
# 检索：拼音
# --------------------------------------------------------------------------- #
def test_pinyin_query_hits_han_name(conn):
    sample = next(
        (i for i in engine.query(conn, "", page_size=100)["items"] if i["name_pinyin"]),
        None,
    )
    if sample is None:
        pytest.skip("库中没有可用的拼音样本")

    by_full = engine.query(conn, sample["name_pinyin"])
    assert any(i["id"] == sample["id"] for i in by_full["items"])
    hit = next(i for i in by_full["items"] if i["id"] == sample["id"])
    assert highlight_util.MARK_OPEN in hit["highlight"]["fields"]["name"]

    if sample["name_initials"]:
        by_initials = engine.query(conn, sample["name_initials"])
        assert any(i["id"] == sample["id"] for i in by_initials["items"])


# --------------------------------------------------------------------------- #
# 筛选：同维度 OR、跨维度 AND（需求十）
# --------------------------------------------------------------------------- #
def test_single_filter_matches_only_that_value(conn):
    province = engine.query(conn, "")["facets"]["province"][0]["value"]
    result = engine.query(conn, "", filters={"province": province})
    assert result["total"] > 0
    assert all(i["province"] == province for i in result["items"])


def test_same_dimension_multiple_values_is_or(conn):
    options = engine.query(conn, "")["facets"]["province"]
    if len(options) < 2:
        pytest.skip("省份不足两个")
    a, b = options[0], options[1]
    result = engine.query(conn, "", filters={"province": [a["value"], b["value"]]})
    assert result["total"] == a["count"] + b["count"]


def test_cross_dimension_is_and(conn):
    base = engine.query(conn, "")
    province = base["facets"]["province"][0]["value"]
    degree = base["facets"]["degree_level"][0]["value"]
    one = engine.query(conn, "", filters={"province": province})
    both = engine.query(conn, "", filters={"province": province, "degree_level": degree})
    assert both["total"] <= one["total"]
    assert all(i["province"] == province and i["degree_level"] == degree
               for i in both["items"])


def test_facet_counts_exclude_own_dimension(conn):
    """选中省份后，省份维度的分面计数不应被自身筛选清零。"""
    base = engine.query(conn, "")
    province = base["facets"]["province"][0]["value"]
    filtered = engine.query(conn, "", filters={"province": province})
    counts = {o["value"]: o["count"] for o in filtered["facets"]["province"]}
    global_counts = {o["value"]: o["count"] for o in base["facets"]["province"]}
    assert counts == global_counts
    assert all(v > 0 for v in counts.values())


def test_selected_flag_reflects_active_filters(conn):
    province = engine.query(conn, "")["facets"]["province"][0]["value"]
    result = engine.query(conn, "", filters={"province": province})
    selected = [o for o in result["facets"]["province"] if o["value"] == province]
    assert selected and selected[0]["selected"] is True


def test_filter_tags_are_removable_descriptors(conn):
    """需求十：`[湖北 ×]` 这种可删除标签。"""
    province = engine.query(conn, "")["facets"]["province"][0]["value"]
    tags = engine.query(conn, "", filters={"province": province})["filter_tags"]
    assert len(tags) == 1
    assert tags[0]["dimension"] == "province"
    assert tags[0]["value"] == province
    assert tags[0]["label"] == "省份"
    assert tags[0]["display"] == shorten_region(province)
    assert tags[0]["display"] != province or "省" not in province


def test_cohort_filter_tag_display_has_suffix(conn):
    year = engine.query(conn, "")["facets"]["cohort_year"][0]["value"]
    tag = engine.query(conn, "", filters={"cohort_year": year})["filter_tags"][0]
    assert tag["display"] == f"{year}届"


# --------------------------------------------------------------------------- #
# 排序（需求 11.2）
# --------------------------------------------------------------------------- #
def test_name_pinyin_sort_is_ascending(conn):
    names = [i["name_pinyin"] for i in
             engine.query(conn, "", sort="name_pinyin", page_size=100)["items"]
             if i["name_pinyin"]]
    assert names == sorted(names)


def test_cohort_asc_sort(conn):
    years = [i["cohort_year"] for i in
             engine.query(conn, "", sort="cohort_asc", page_size=100)["items"]
             if i["cohort_year"]]
    assert years == sorted(years)


@pytest.mark.parametrize("sort", ["relevance", "cohort_desc", "cohort_asc",
                                  "name_pinyin", "confidence_desc"])
def test_every_sort_key_is_accepted(conn, sort: str):
    assert engine.query(conn, "", sort=sort)["sort"] == sort


def test_default_sort_depends_on_having_keywords(conn):
    """无关键词→默认届别倒序；有关键词→相关度。"""
    assert engine.query(conn, "")["sort"] == "cohort_desc"
    assert engine.query(conn, "选调")["sort"] == "relevance"
    assert engine.query(conn, "", sort="cohort_asc")["sort"] == "cohort_asc"


# --------------------------------------------------------------------------- #
# 分页（需求 11.3）
# --------------------------------------------------------------------------- #
def test_pagination_math(conn, record_count):
    result = engine.query(conn, "", page=1, page_size=20)
    assert result["pages"] == math.ceil(record_count / 20)
    assert result["has_prev"] is False
    assert result["has_next"] is True


def test_pages_do_not_overlap(conn):
    p1 = engine.query(conn, "", page=1, page_size=20)
    p2 = engine.query(conn, "", page=2, page_size=20)
    assert not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})


def test_page_size_is_clamped(conn):
    assert engine.query(conn, "", page_size=9999)["page_size"] == engine.MAX_PAGE_SIZE
    # 负数夹到下限 1；0 / None 视为“未指定”，用默认每页条数
    assert engine.query(conn, "", page_size=-5)["page_size"] == 1
    assert engine.query(conn, "", page_size=0)["page_size"] == engine.DEFAULT_PAGE_SIZE
    assert engine.query(conn, "", page_size=None)["page_size"] == engine.DEFAULT_PAGE_SIZE


def test_invalid_page_falls_back_to_first(conn):
    result = engine.query(conn, "", page="abc")
    assert result["page"] == 1


# --------------------------------------------------------------------------- #
# 多关键词
# --------------------------------------------------------------------------- #
def test_multi_keyword_reports_groups(conn):
    result = engine.query(conn, "选调 本科")
    assert len(result["groups"]) == 2
    assert result["mode"] in ("and", "or")
    assert all("term" in g and "total" in g for g in result["groups"])


# --------------------------------------------------------------------------- #
# 模糊匹配（config/fuzzy_terms.yaml + src/search/fuzzy.py）
# --------------------------------------------------------------------------- #
#: 词表里保证存在的例子：搜简写「信工」要命中库中写的「信息工程学院」
FUZZY_ALIAS = "信工"
FUZZY_CANONICAL = "信息工程学院"


def test_fuzzy_terms_are_loaded(conn):
    result = engine.query(conn, FUZZY_ALIAS)
    assert result["search_terms"][0] == FUZZY_ALIAS          # 用户原词永远排第一
    assert FUZZY_CANONICAL in result["search_terms"]        # 扩展出了库里的写法


def test_fuzzy_alias_finds_canonical_records(conn):
    fuzzy = engine.query(conn, FUZZY_ALIAS)
    canonical = engine.query(conn, FUZZY_CANONICAL)
    assert fuzzy["total"] > 0
    assert fuzzy["mode"] == "fuzzy"
    # 简写与全称命中同一批记录
    assert fuzzy["total"] == canonical["total"]
    assert {i["id"] for i in fuzzy["items"]} <= {i["id"] for i in canonical["items"]}
    assert all(i["college"] == FUZZY_CANONICAL for i in fuzzy["items"])


def test_fuzzy_expansion_is_explained_to_frontend(conn):
    groups = engine.query(conn, FUZZY_ALIAS)["term_groups"]
    assert len(groups) == 1
    assert groups[0]["term"] == FUZZY_ALIAS
    assert FUZZY_CANONICAL in groups[0]["added"]
    assert groups[0]["variants"][0] == FUZZY_ALIAS


def test_exact_canonical_query_stays_on_fast_path(conn):
    """直接敲正式写法时行为不能变：仍是 FTS + bm25，也不展示扩展说明。"""
    result = engine.query(conn, FUZZY_CANONICAL)
    assert result["mode"] == "fts"
    assert result["term_groups"] == []


def test_unknown_term_is_not_expanded(conn):
    result = engine.query(conn, "这个词词表里绝对没有")
    assert result["search_terms"] == ["这个词词表里绝对没有"]
    assert result["term_groups"] == []
    assert result["mode"] in ("fts", "like", "all")


def test_fuzzy_highlights_the_canonical_spelling(conn):
    """高亮必须落在库里的汉字上（而不是「信工」这两个字）。"""
    result = engine.query(conn, FUZZY_ALIAS)
    marks = [v for i in result["items"]
             for v in i["highlight"]["fields"].values()
             if highlight_util.MARK_OPEN in v]
    assert marks
    assert all(FUZZY_CANONICAL in m for m in marks)


def test_fuzzy_facets_cover_only_the_expanded_hits(conn):
    result = engine.query(conn, FUZZY_ALIAS)
    college = [f for f in result["facets"]["college"] if f["value"] == FUZZY_CANONICAL]
    assert college and college[0]["count"] == result["total"]


def test_fuzzy_filters_apply_to_expanded_hits(conn):
    base = engine.query(conn, FUZZY_ALIAS)
    province = base["items"][0]["province"]
    narrowed = engine.query(conn, FUZZY_ALIAS, filters={"province": province})
    assert narrowed["mode"] == "fuzzy"
    assert 0 < narrowed["total"] <= base["total"]
    assert all(i["province"] == province for i in narrowed["items"])


def test_fuzzy_alias_with_second_keyword_keeps_and_semantics(conn):
    """「信工 选调」不能在词这一层求交：扩展出的「信工院」零命中会把交集打穿。"""
    result = engine.query(conn, f"{FUZZY_ALIAS} 选调")
    assert result["mode"] == "and"
    assert len(result["groups"]) == 2                      # 两个用户词 → 两组
    assert result["groups"][0]["term"] == FUZZY_ALIAS      # 组内是并集后的命中数
    assert result["total"] > 0


def test_fuzzy_pagination_is_stable(conn):
    total = engine.query(conn, FUZZY_ALIAS)["total"]
    if total < 2:
        pytest.skip("命中数太少，无法比较分页")
    first = engine.query(conn, FUZZY_ALIAS, page_size=1)
    second = engine.query(conn, FUZZY_ALIAS, page=2, page_size=1)
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["total"] == second["total"] == total


def test_suggestions_expose_alias_pairs(conn):
    data = engine.suggestions(conn)
    pairs = {(p["alias"], p["canonical"]) for p in data["aliases"]}
    assert (FUZZY_ALIAS, FUZZY_CANONICAL) in pairs
    assert data["fuzzy"]["available"] is True


# --------------------------------------------------------------------------- #
# 高亮（需求十二）
# --------------------------------------------------------------------------- #
def test_every_item_has_highlight(conn):
    province = engine.query(conn, "")["facets"]["province"][0]["value"]
    result = engine.query(conn, province)
    assert all("fields" in i["highlight"] for i in result["items"])
    assert any(highlight_util.MARK_OPEN in v for i in result["items"]
               for v in i["highlight"]["fields"].values())


def test_highlight_never_leaks_raw_html(conn):
    province = engine.query(conn, "")["facets"]["province"][0]["value"]
    for item in engine.query(conn, province)["items"]:
        for value in item["highlight"]["fields"].values():
            assert "<script" not in value


def test_highlight_can_be_disabled(conn):
    result = engine.query(conn, "选调", with_highlight=False)
    assert all(i["highlight"]["matched_fields"] == [] for i in result["items"])


# --------------------------------------------------------------------------- #
# 详情（需求十四）
# --------------------------------------------------------------------------- #
def test_detail_contains_everything_the_page_needs(conn):
    record_id = engine.query(conn, "")["items"][0]["id"]
    detail = engine.get_detail(conn, record_id)
    assert detail is not None
    for field in ("name", "college", "major", "degree", "cohort", "cohort_year",
                  "province", "city", "destination_org", "position",
                  "notice_title", "organization", "release_time",
                  "image_url", "highlight"):
        assert field in detail, field
    assert len(detail["field_labels"]) == 10
    assert detail["source_text"] or detail["raw_text"]
    assert detail["source_text"] is None or isinstance(detail["source_text"], str)


def test_detail_returns_none_for_missing_record(conn):
    assert engine.get_detail(conn, 99999999) is None


# --------------------------------------------------------------------------- #
# 统计（需求十五）
# --------------------------------------------------------------------------- #
def test_suggestions_cover_all_input_helpers(conn):
    data = engine.suggestions(conn)
    assert data["provinces"] and data["colleges"] and data["majors"]
    assert isinstance(data["pinyin"], bool)