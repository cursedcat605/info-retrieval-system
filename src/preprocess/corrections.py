"""人工纠错层：把"手工修正"变成可重跑、可进 git 的配置。

OCR 与正则规则都不可能 100% 正确，但**直接改数据是不可持续的**：

====================  ==========================  ================================
文件                  谁生成                        重跑后会怎样
====================  ==========================  ================================
``ocr_text/*.json``    ``run_ocr.py``               ``--force`` 时被覆盖
``processed/*``        ``extract_records.py``       每次全量重写
``db/selects.sqlite``  ``build_database.py``        ``--reset`` 时清空重建
====================  ==========================  ================================

于是把人工修正集中到 ``config/corrections.yaml``，在**每次都会重跑的流水线**里
生效 —— 既改得动数据，又改不丢，还能在 PR 里 review。

两层规则，各管一类问题：

- **文本层** (:meth:`Corrections.fix_text`)：在"清洗后、字段抽取前"替换文本。
  适合 OCR 固定认错的字（``选调牛`` → ``选调生``），或把被粘在一起的行拆开。
- **字段层** (:meth:`Corrections.override_fields`)：抽取完成后直接改写字段值。
  适合"**文本没问题，是切分规则切错了**"的情况（例如城市正则把上一行的
  ``…本科生`` 和 ``湛江`` 粘成了 ``科生湛江市``）。

规则按 ``text.global`` → ``text.images.<图片>`` → ``fields.<图片>`` 的顺序叠加，
后写的覆盖先写的。**所有规则都会统计命中次数**，跑完后报告未命中的规则 ——
否则改完 yaml 发现毫无变化，会误以为是流水线没生效。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from ..utils.logger import get_logger
from ..utils.paths import project_root
from .cleaner import normalize_text

logger = get_logger("corrections")

#: 默认规则文件（相对项目根目录）
DEFAULT_PATH = "config/corrections.yaml"

#: 允许被字段规则覆盖的列。与 ``selects_records`` 保持一致 ——
#: 白名单既防手滑（``cities`` / ``collage``），也让报错发生在加载时而非入库时。
OVERRIDABLE_FIELDS: Tuple[str, ...] = (
    "name",
    "cohort",
    "cohort_year",
    "college",
    "major",
    "destination_org",
    "city",
    "province",
    "position",
    "degree",
)


def normalize_image(image: Optional[str]) -> str:
    """把图片名归一为不含扩展名的 stem，便于 yaml 里怎么写都能对上。"""
    if not image:
        return ""
    name = str(image).strip().replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".JPG", ".JPEG", ".PNG"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


# --------------------------------------------------------------------------- #
# 单条规则
# --------------------------------------------------------------------------- #
@dataclass
class TextRule:
    """一条文本替换规则。"""

    source: str
    target: str
    image: Optional[str] = None  # None 表示全局
    is_regex: bool = False
    origin: str = ""
    hits: int = 0

    def apply(self, text: str) -> str:
        """替换并把命中次数累计到 :attr:`hits`。"""
        if self.is_regex:
            new_text, count = re.subn(self.source, self.target, text)
        else:
            count = text.count(self.source)
            new_text = text.replace(self.source, self.target) if count else text
        self.hits += count
        return new_text

    def describe(self) -> str:
        scope = "全局" if self.image is None else self.image
        arrow = f"{self.source} → {self.target}"
        if self.is_regex:
            arrow = f"re:{arrow}"
        return f"[文本/{scope}] {arrow}"


@dataclass
class FieldRule:
    """一条字段覆盖规则。

    定位方式二选一：

    - ``name``：按姓名匹配记录（**推荐**，可读且不随规则调整而漂移）；
    - ``block_index``：按"该图片内的第几个记录"匹配（姓名本身也错时的兜底）。
    """

    image: str
    field: str
    value: Any
    name: Optional[str] = None
    block_index: Optional[int] = None
    origin: str = ""
    hits: int = 0

    def matches(self, image: str, name: Optional[str], block_index: int) -> bool:
        if normalize_image(image) != self.image:
            return False
        if self.name is not None:
            return (name or "").strip() == self.name
        if self.block_index is not None:
            return block_index == self.block_index
        return False

    def describe(self) -> str:
        who = self.name if self.name is not None else f"#{self.block_index}"
        return f"[字段/{self.image}] {who} · {self.field} = {self.value!r}"


# --------------------------------------------------------------------------- #
# 规则集合
# --------------------------------------------------------------------------- #
@dataclass
class Corrections:
    """一堆规则的集合，外加命中统计。"""

    text_rules: List[TextRule] = field(default_factory=list)
    field_rules: List[FieldRule] = field(default_factory=list)
    source_path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)
    _seen_images: set = field(default_factory=set, repr=False)

    # ---- 查询 ----
    @property
    def is_empty(self) -> bool:
        return not self.text_rules and not self.field_rules

    def has_text_rules(self) -> bool:
        return bool(self.text_rules)

    # ---- 文本层 ----
    def fix_text(self, image: Optional[str], text: str) -> str:
        """把全局规则与该图片的规则依次作用于一段文本。"""
        if not self.text_rules or not text:
            return text
        stem = normalize_image(image)
        for rule in self.text_rules:
            if rule.image is not None and rule.image != stem:
                continue
            text = rule.apply(text)
        return text

    def fix_line(self, image: Optional[str], raw: str) -> Tuple[str, bool]:
        """修正一行 OCR 文本，返回 ``(新文本, 是否改动)``。

        先在**原文**上匹配（这样 ``from`` 可以直接照抄详情页"原文"里看到的字），
        没命中再用 ``normalize_text`` **归一化后**的文本试一次 —— 于是全角/半角、
        不可见字符、常见错字都不会影响你写规则。

        两次都未命中时返回原文，保证无规则的图片与引入纠错层之前逐字节一致。
        """
        if not self.text_rules or not raw:
            return raw, False
        fixed = self.fix_text(image, raw)
        if fixed != raw:
            return fixed, True
        base = normalize_text(raw)
        if base == raw:
            return raw, False
        fixed = self.fix_text(image, base)
        if fixed != base:
            return fixed, True
        return raw, False

    # ---- 字段层 ----
    def override_fields(
        self,
        image: Optional[str],
        name: Optional[str],
        block_index: int,
        row: Dict[str, Any],
    ) -> List[str]:
        """就地覆盖 ``row``，返回被改动的字段名（去重、保持顺序）。

        ``hits`` 统计的是**匹配到记录**的次数，而不是"值真的变了"的次数 ——
        否则同一条规则在数据已修好之后会一直显示为"未命中"，让人误以为规则失效。
        """
        changed: List[str] = []
        for rule in self.field_rules:
            if not rule.matches(image or "", name, block_index):
                continue
            rule.hits += 1
            if row.get(rule.field) == rule.value:
                continue  # 值本来就对：算命中，但不产生改动
            row[rule.field] = rule.value
            if rule.field not in changed:
                changed.append(rule.field)
        return changed

    # ---- 校验 ----
    def check_images(self, known_images: Iterable[str]) -> None:
        """把"图片名写错"这类问题变成显式告警。"""
        known = {normalize_image(i) for i in known_images}
        for image in sorted(self._seen_images - known):
            self.warnings.append(
                f"规则里的图片 {image!r} 在 OCR 结果里找不到（检查文件名是否写错）"
            )

    def check_field_duplicates(self) -> None:
        """同一图片、同一字段被多条规则命中时报出来 —— 通常意味着写重了。"""
        seen: Dict[Tuple[str, str], int] = {}
        for rule in self.field_rules:
            key = (rule.image, rule.field)
            seen[key] = seen.get(key, 0) + 1
        for (image, dim), count in sorted(seen.items()):
            if count > 1:
                self.warnings.append(
                    f"{image} 的 {dim} 字段有 {count} 条覆盖规则，最后一条会生效"
                )

    # ---- 报告 ----
    def report(self) -> Tuple[List[str], List[str]]:
        """返回 ``(已命中的规则描述, 未命中的规则描述)``。"""
        hit: List[str] = []
        miss: List[str] = []
        for rule in [*self.text_rules, *self.field_rules]:
            (hit if rule.hits else miss).append(rule.describe())
        return hit, miss


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #
def _as_list(node: Any, where: str, warnings: List[str]) -> List[Any]:
    if node is None:
        return []
    if not isinstance(node, list):
        warnings.append(f"{where} 应该是列表，已忽略")
        return []
    return node


def _build_text_rule(raw: Dict[str, Any], image: Optional[str], index: int,
                     warnings: List[str]) -> Optional[TextRule]:
    where = f"text.{'global' if image is None else image}[{index}]"
    if not isinstance(raw, dict):
        warnings.append(f"{where} 不是映射，已忽略")
        return None
    source = raw.get("from")
    target = raw.get("to")
    if not isinstance(source, str) or not source:
        warnings.append(f"{where} 缺少非空的 from，已忽略")
        return None
    if not isinstance(target, str):
        warnings.append(f"{where} 缺少 to（想删除该片段就写空字符串 \"\"），已忽略")
        return None
    is_regex = bool(raw.get("regex", False))
    if is_regex:
        try:
            re.compile(source)
        except re.error as exc:
            warnings.append(f"{where} 的正则无法编译（{exc}），已忽略")
            return None
    elif source == target:
        warnings.append(f"{where} 的 from 与 to 相同，是空规则，已忽略")
        return None
    return TextRule(
        source=source,
        target=target,
        image=image,
        is_regex=is_regex,
        origin=where,
    )


def _build_field_rule(raw: Dict[str, Any], image: str, index: int,
                      warnings: List[str]) -> List[FieldRule]:
    """把一条 yaml 条目展开成若干 :class:`FieldRule`。

    支持两种写法：:

        # ① 扁平：除 name / block_index 外的键都当作"要覆盖的字段"
        - name: 宋宇欣            # 定位（抽取出来的姓名）
          city: 湛江市

        # ② 嵌套：用 set: 显式列出要覆盖的字段，因此**可以改 name 本身**
        - name: 张三              # 定位（抽取出来的、写错的姓名）
          set:
            name: 张叁
            city: 湛江市

    没有 ``set`` 时 ``name`` 只作定位符 —— 否则"定位用 name"和"要改 name"
    无法区分。
    """
    where = f"fields.{image}[{index}]"
    if not isinstance(raw, dict):
        warnings.append(f"{where} 不是映射，已忽略")
        return []

    name = raw.get("name")
    block_index = raw.get("block_index")
    if name is not None:
        name = str(name).strip()
    if block_index is not None:
        try:
            block_index = int(block_index)
        except (TypeError, ValueError):
            warnings.append(f"{where} 的 block_index 不是整数，已忽略")
            return []
    if name is None and block_index is None:
        warnings.append(f"{where} 必须写明 name 或 block_index 之一（用于定位记录），已忽略")
        return []

    nested = raw.get("set")
    if nested is not None:
        if not isinstance(nested, dict) or not nested:
            warnings.append(f"{where} 的 set 应该是非空映射（字段名 → 新值），已忽略")
            return []
        targets: Dict[str, Any] = dict(nested)
    else:
        reserved = {"name", "block_index", "set", "note"}
        targets = {k: v for k, v in raw.items() if k not in reserved}

    if not targets:
        warnings.append(f"{where} 没有指定任何要覆盖的字段，已忽略")
        return []

    rules: List[FieldRule] = []
    for key, value in targets.items():
        if key not in OVERRIDABLE_FIELDS:
            warnings.append(
                f"{where} 的字段 {key!r} 不可覆盖（可选：{', '.join(OVERRIDABLE_FIELDS)}）"
            )
            continue
        rules.append(
            FieldRule(
                image=image,
                field=key,
                value=value,
                name=name,
                block_index=block_index,
                origin=f"{where}.{key}",
            )
        )
    return rules


def load_corrections(path: Optional[str | Path] = None) -> Corrections:
    """读取 ``config/corrections.yaml``。

    :param path: 规则文件路径；缺省为项目根目录下的 :data:`DEFAULT_PATH`，
        传空字符串或 ``None`` 时走默认值。文件不存在时返回空规则集（不报错）。
    :raises ValueError: YAML 语法错误，或顶层不是映射。
    """
    root = project_root()
    target = Path(path) if path else root / DEFAULT_PATH
    if not target.is_absolute():
        target = root / target

    corrections = Corrections(source_path=target)
    if not target.exists():
        return corrections

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"纠错文件 {target} 的 YAML 语法错误：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"纠错文件 {target} 的顶层应该是映射（键：text / fields）")

    # ---- ① 文本层 ----
    text_node = raw.get("text") or {}
    if isinstance(text_node, dict):
        for i, item in enumerate(_as_list(text_node.get("global"), "text.global",
                                          corrections.warnings)):
            rule = _build_text_rule(item, None, i, corrections.warnings)
            if rule:
                corrections.text_rules.append(rule)
        images_node = text_node.get("images") or {}
        if isinstance(images_node, dict):
            for image_key, items in images_node.items():
                stem = normalize_image(image_key)
                corrections._seen_images.add(stem)
                # 先全局、后按图片：列表顺序即优先级
                for i, item in enumerate(_as_list(items, f"text.images.{image_key}",
                                                  corrections.warnings)):
                    rule = _build_text_rule(item, stem, i, corrections.warnings)
                    if rule:
                        corrections.text_rules.append(rule)
        elif images_node:
            corrections.warnings.append("text.images 应该是映射（图片名 → 规则列表）")
    elif text_node:
        corrections.warnings.append("text 应该是映射（含 global / images 两个键）")

    # ---- ② 字段层 ----
    fields_node = raw.get("fields") or {}
    if isinstance(fields_node, dict):
        for image_key, items in fields_node.items():
            stem = normalize_image(image_key)
            corrections._seen_images.add(stem)
            for i, item in enumerate(_as_list(items, f"fields.{image_key}",
                                              corrections.warnings)):
                corrections.field_rules.extend(
                    _build_field_rule(item, stem, i, corrections.warnings)
                )
    elif fields_node:
        corrections.warnings.append("fields 应该是映射（图片名 → 规则列表）")

    corrections.check_field_duplicates()
    for message in corrections.warnings:
        logger.warning("纠错层：%s", message)
    logger.info(
        "纠错层已加载 %s：文本规则 %d 条、字段规则 %d 条",
        target.name, len(corrections.text_rules), len(corrections.field_rules),
    )
    return corrections


__all__ = [
    "DEFAULT_PATH",
    "OVERRIDABLE_FIELDS",
    "Corrections",
    "FieldRule",
    "TextRule",
    "apply_field_corrections",
    "apply_line_corrections",
    "load_corrections",
    "normalize_image",
]


# --------------------------------------------------------------------------- #
# 流水线入口
# --------------------------------------------------------------------------- #
def apply_line_corrections(
    result: Any,
    corrections: Optional[Corrections],
) -> int:
    """就地把 :class:`src.ocr.engine.OcrResult` 的每一行文本过一遍文本规则。

    因为 ``OcrResult.text`` 是**由 lines 派生的属性**，在这里改写 ``line.text``
    就能同时改到下游的两条链路：字段抽取（``extract_from_ocr_result``）与
    ``data/processed/ocr_clean.txt``。也因此只需一次应用，不会重复替换。

    :param result: 鸭子类型的 OCR 结果对象（需有 ``image`` 与 ``lines``）。
    :param corrections: 规则集；``None`` 或空规则集时直接返回 0（零开销）。
    :return: 被改动的行数。
    """
    if corrections is None or not corrections.has_text_rules():
        return 0
    image = getattr(result, "image", None)
    changed = 0
    for line in getattr(result, "lines", None) or []:
        raw = getattr(line, "text", "") or ""
        if not raw:
            continue
        fixed, touched = corrections.fix_line(image, raw)
        if touched:
            line.text = fixed
            changed += 1
    return changed


def apply_field_corrections(
    row: Dict[str, Any],
    corrections: Optional[Corrections],
) -> List[str]:
    """就地对一行待入库的结构化记录应用字段覆盖规则。

    :param row: :func:`scripts.extract_records.record_to_row` 产出的字典，
        需要含 ``image`` / ``name`` / ``block_index``。
    :param corrections: 规则集；``None`` 或空规则集时直接返回空列表。
    :return: 实际被改写的字段名。
    """
    if corrections is None or not corrections.field_rules:
        return []
    return corrections.override_fields(
        image=row.get("image"),
        name=row.get("name"),
        block_index=int(row.get("block_index") or 0),
        row=row,
    )
