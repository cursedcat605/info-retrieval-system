"""人工纠错层（``config/corrections.yaml``）的单元测试。

全部是纯函数 / 临时文件测试，不依赖数据库，也不依赖真实的 OCR 结果。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ocr.engine import OcrLine, OcrResult  # noqa: E402
from src.preprocess.corrections import (  # noqa: E402
    OVERRIDABLE_FIELDS,
    Corrections,
    FieldRule,
    TextRule,
    apply_field_corrections,
    apply_line_corrections,
    load_corrections,
    normalize_image,
)

IMAGE = "20231129_238345_01_广东选调.jpg"
STEM = "20231129_238345_01_广东选调"


def write_rules(tmp_path: Path, body: str) -> Path:
    """把 yaml 正文写到临时文件并返回路径。"""
    path = tmp_path / "corrections.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 图片名归一
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a.jpg", "a"),
        ("a.JPG", "a"),
        ("dir/a.png", "a"),
        ("dir\\a.jpg", "a"),
        ("a", "a"),
        ("  广东选调.jpg  ", "广东选调"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_image(raw, expected):
    assert normalize_image(raw) == expected


# --------------------------------------------------------------------------- #
# 文本层
# --------------------------------------------------------------------------- #
def test_text_rule_replaces_substring(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {from: 选调牛, to: 选调生}\n")
    )
    assert corrections.fix_text(IMAGE, "2024届福建选调牛") == "2024届福建选调生"


def test_text_rule_is_scoped_to_one_image(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"text:\n  images:\n    {STEM}:\n      - {{from: 科生湛江, to: 湛江}}\n",
        )
    )
    # 目标图片：命中
    assert corrections.fix_text(IMAGE, "法学院2019级本科生科生湛江市委政法委") == (
        "法学院2019级本科生湛江市委政法委"
    )
    # 其它图片：不命中，原样返回
    assert corrections.fix_text("别的图片.jpg", "科生湛江市委政法委") == "科生湛江市委政法委"
    # 图片名不带扩展名也能对上
    assert corrections.fix_text(STEM, "科生湛江") == "湛江"
    # image=None（无归属文本）只看全局规则，不看图片规则
    assert corrections.fix_text(None, "科生湛江") == "科生湛江"


def test_text_rule_regex(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            "text:\n  global:\n    - {regex: true, from: '第(\\d)届', to: '20\\1届'}\n",
        )
    )
    assert corrections.fix_text(IMAGE, "第4届") == "204届"


def test_fix_line_prefers_raw_then_normalized(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {from: ＡＢＣ, to: ABC}\n")
    )
    # 原文就能命中
    assert corrections.fix_line(IMAGE, "xxＡＢＣyy") == ("xxABCyy", True)
    # 原文命不中，但归一化（全角→半角）之后命中
    corrections2 = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {from: ABC, to: XYZ}\n")
    )
    raw = "xxＡＢＣyy"
    fixed, touched = corrections2.fix_line(IMAGE, raw)
    assert touched
    assert fixed == "xxXYZyy"


def test_fix_line_untouched_returns_original_object_value(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {from: 不存在, to: 无关}\n")
    )
    raw = "法学院2019级法学专业本科生"
    assert corrections.fix_line(IMAGE, raw) == (raw, False)


def test_text_rule_hits_are_counted(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {from: aa, to: b}\n")
    )
    assert corrections.fix_text(IMAGE, "aaaa") == "bb"
    # 非重叠计数，"aaaa".count("aa") == 2，与 str.replace 的替换次数一致
    assert corrections.text_rules[0].hits == 2
    assert corrections.fix_text(IMAGE, "无关") == "无关"
    assert corrections.text_rules[0].hits == 2


def test_global_rules_run_before_image_rules(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"text:\n  global:\n    - {{from: A, to: B}}\n"
            f"  images:\n    {STEM}:\n      - {{from: B, to: C}}\n",
        )
    )
    assert corrections.fix_text(IMAGE, "A") == "C"


# --------------------------------------------------------------------------- #
# apply_line_corrections：改写 OcrResult 的行
# --------------------------------------------------------------------------- #
def test_apply_line_corrections_rewrites_lines_and_derived_text(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {from: 科生湛江, to: 湛江}\n")
    )
    result = OcrResult(
        image=IMAGE,
        lines=[
            OcrLine(text="法学院2019级法学专业本科生", score=0.9, box=[[0, 0], [1, 0], [1, 1], [0, 1]]),
            OcrLine(text="科生湛江市委政法委", score=0.8, box=[[0, 2], [1, 2], [1, 3], [0, 3]]),
        ],
    )
    changed = apply_line_corrections(result, corrections)
    assert changed == 1
    assert result.lines[1].text == "湛江市委政法委"
    # box / score 必须保留
    assert result.lines[1].score == pytest.approx(0.8)
    assert result.lines[1].box == [[0, 2], [1, 2], [1, 3], [0, 3]]
    # text 是由 lines 派生的属性，应当自动跟着变
    assert result.text == "法学院2019级法学专业本科生\n湛江市委政法委"


def test_apply_line_corrections_is_noop_without_rules():
    result = OcrResult(image=IMAGE, lines=[OcrLine(text="科生湛江")])
    assert apply_line_corrections(result, None) == 0
    assert apply_line_corrections(result, Corrections()) == 0
    assert result.lines[0].text == "科生湛江"


def test_apply_line_corrections_runs_once_per_rule(tmp_path):
    """改写过的行不应被同一条规则二次匹配。"""
    corrections = Corrections()
    corrections.text_rules.append(TextRule(source="科生湛江", target="湛江"))
    result = OcrResult(image=IMAGE, lines=[OcrLine(text="科生湛江")])
    assert apply_line_corrections(result, corrections) == 1
    assert result.lines[0].text == "湛江"
    # 再跑一遍：已无可替换内容，应返回 0
    assert apply_line_corrections(result, corrections) == 0
    assert result.lines[0].text == "湛江"


# --------------------------------------------------------------------------- #
# 字段层
# --------------------------------------------------------------------------- #
def row(name: str, block_index: int = 0, **extra):
    base = {"image": IMAGE, "name": name, "block_index": block_index, "city": None}
    base.update(extra)
    return base


def test_field_override_by_name(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"fields:\n  {STEM}:\n    - name: 王小明\n      city: 湛江市\n",
        )
    )
    target = row("王小明", city="科生湛江市")
    assert apply_field_corrections(target, corrections) == ["city"]
    assert target["city"] == "湛江市"

    other = row("张三", city="科生湛江市")
    assert apply_field_corrections(other, corrections) == []
    assert other["city"] == "科生湛江市"


def test_field_override_by_block_index(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"fields:\n  {STEM}:\n    - block_index: 2\n      city: 湛江市\n",
        )
    )
    assert apply_field_corrections(row("谁", block_index=2, city="科生湛江市"),
                                   corrections) == ["city"]
    assert apply_field_corrections(row("谁", block_index=1, city="科生湛江市"),
                                   corrections) == []


def test_field_override_matches_image_with_or_without_extension(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"fields:\n  {IMAGE}:\n    - name: 王小明\n      city: 湛江市\n",
        )
    )
    assert apply_field_corrections(row("王小明", city=None), corrections) == ["city"]


def test_nested_set_can_override_name_itself(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"fields:\n  {STEM}:\n    - name: 张三\n      set: {{name: 张叁, city: 湛江市}}\n",
        )
    )
    target = row("张三")
    changed = apply_field_corrections(target, corrections)
    assert set(changed) == {"name", "city"}
    assert target["name"] == "张叁"
    assert target["city"] == "湛江市"


def test_field_hits_counted_even_when_value_already_correct(tmp_path):
    """值本来就对也算命中 —— 否则修好之后规则会一直显示为"未命中"。"""
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"fields:\n  {STEM}:\n    - name: 王小明\n      city: 湛江市\n",
        )
    )
    target = row("王小明", city="湛江市")
    assert apply_field_corrections(target, corrections) == []
    assert corrections.field_rules[0].hits == 1


def test_apply_field_corrections_noop_without_rules():
    target = row("王小明", city="科生湛江市")
    assert apply_field_corrections(target, None) == []
    assert apply_field_corrections(target, Corrections()) == []
    assert target["city"] == "科生湛江市"


# --------------------------------------------------------------------------- #
# 加载 / 校验
# --------------------------------------------------------------------------- #
def test_missing_file_is_empty_noop(tmp_path):
    corrections = load_corrections(tmp_path / "nope.yaml")
    assert corrections.is_empty
    assert corrections.source_path == tmp_path / "nope.yaml"
    assert corrections.fix_text(IMAGE, "科生湛江") == "科生湛江"
    assert apply_line_corrections(OcrResult(image=IMAGE, lines=[OcrLine(text="科生湛江")]),
                                  corrections) == 0


def test_default_path_points_into_repo():
    corrections = load_corrections(None)
    assert corrections.source_path is not None
    assert corrections.source_path.parts[-2:] == ("config", "corrections.yaml")


def test_relative_path_resolves_against_project_root():
    corrections = load_corrections("config/corrections.yaml")
    assert corrections.source_path is not None
    assert corrections.source_path.is_absolute()


def test_unknown_field_is_warned_and_skipped(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"fields:\n  {STEM}:\n    - name: 王小明\n      collage: 文学院\n",
        )
    )
    assert corrections.field_rules == []
    assert any("collage" in w for w in corrections.warnings)


def test_rule_without_locator_is_warned(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, f"fields:\n  {STEM}:\n    - city: 湛江市\n")
    )
    assert corrections.field_rules == []
    assert any("定位" in w for w in corrections.warnings)


def test_empty_target_is_warned(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, f"fields:\n  {STEM}:\n    - name: 王小明\n")
    )
    assert corrections.field_rules == []
    assert any("没有指定" in w for w in corrections.warnings)


def test_text_rule_ignored_when_from_equals_to(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {from: 相同, to: 相同}\n")
    )
    assert corrections.text_rules == []
    assert any("空规则" in w for w in corrections.warnings)


def test_bad_regex_is_warned_not_raised(tmp_path):
    corrections = load_corrections(
        write_rules(tmp_path, "text:\n  global:\n    - {regex: true, from: '[', to: x}\n")
    )
    assert corrections.text_rules == []
    assert any("正则" in w for w in corrections.warnings)


def test_broken_yaml_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="YAML 语法错误"):
        load_corrections(write_rules(tmp_path, "text: [\n"))


def test_non_mapping_root_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="顶层"):
        load_corrections(write_rules(tmp_path, "- just\n- a list\n"))


def test_check_images_flags_typo_in_filename(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            "fields:\n  不存在的图片:\n    - name: 王小明\n      city: 湛江市\n",
        )
    )
    corrections.check_images([IMAGE])
    assert any("找不到" in w for w in corrections.warnings)


def test_duplicate_field_rules_are_flagged(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"fields:\n  {STEM}:\n    - name: 王小明\n      city: 湛江市\n"
            f"    - name: 王小明\n      city: 湛江市区\n",
        )
    )
    assert any("有 2 条覆盖规则" in w for w in corrections.warnings)


def test_report_splits_matched_and_unmatched(tmp_path):
    corrections = load_corrections(
        write_rules(
            tmp_path,
            f"text:\n  global:\n    - {{from: 科生湛江, to: 湛江}}\n"
            f"    - {{from: 永远不出现, to: x}}\n"
            f"fields:\n  {STEM}:\n    - name: 王小明\n      city: 湛江市\n",
        )
    )
    apply_line_corrections(
        OcrResult(image=IMAGE, lines=[OcrLine(text="科生湛江市委政法委")]), corrections
    )
    apply_field_corrections(row("王小明"), corrections)
    hit, miss = corrections.report()
    assert len(hit) == 2
    assert len(miss) == 1
    assert "永远不出现" in miss[0]


# --------------------------------------------------------------------------- #
# 与文档一致性
# --------------------------------------------------------------------------- #
def _selects_records_columns() -> set:
    """从 DDL 里解析出 ``selects_records`` 的列名集合。"""
    from src.database.schema import DDL_STATEMENTS  # noqa: PLC0415

    ddl = next(s for s in DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS selects_records" in s)
    body = ddl.split("(", 1)[1].rsplit(")", 1)[0]
    columns = set()
    for line in body.splitlines():
        line = line.split("--")[0].strip()
        if not line or line.upper().startswith(("UNIQUE", "PRIMARY", "FOREIGN", "CHECK")):
            continue
        match = re.match(r"(\w+)\s+[A-Z]", line)
        if match:
            columns.add(match.group(1))
    return columns


def test_overridable_fields_are_real_database_columns():
    """白名单必须都是 ``selects_records`` 里真实存在的列。"""
    columns = _selects_records_columns()
    assert columns, "未能从 DDL 解析出列名，测试本身需要更新"
    missing = [f for f in OVERRIDABLE_FIELDS if f not in columns]
    assert missing == []


def test_overridable_fields_exclude_identity_and_derived_columns():
    """定位列（image / block_index）与派生列不允许被手工覆盖。"""
    forbidden = {"id", "image", "block_index", "evidence", "raw_text", "confidence"}
    assert forbidden.isdisjoint(OVERRIDABLE_FIELDS)


def test_config_example_loads_cleanly():
    """仓库里自带的 config/corrections.yaml 必须能加载且无告警之外的错。"""
    corrections = load_corrections(None)
    assert isinstance(corrections, Corrections)
    assert corrections.warnings == []


def test_field_rule_dataclass_defaults():
    rule = FieldRule(image="a", field="city", value="湛江市", name="王小明")
    assert rule.hits == 0
    assert rule.matches("a.jpg", "王小明", 0)
    assert not rule.matches("b.jpg", "王小明", 0)
