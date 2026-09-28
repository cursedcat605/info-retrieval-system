"""结果高亮：把命中关键词在字段里标出来。

需求十二要求"用户能快速判断一条结果为什么会出现在当前检索结果中"，
因此高亮范围不局限于姓名/岗位，而是覆盖全部可检索字段，并附上正文片段。

实现要点
--------
* **先转义、再标记**：服务端先把字段做 HTML 转义，再插入 ``<mark>``，
  前端可直接 ``innerHTML`` 渲染而不引入 XSS；
* **词典序替换**：把多个关键词按长度降序拼成正则，保证"软件工程"优先于"软件"，
  避免长词被短词切断；
* **拼音命中回写**：用户搜 ``zhangsan`` 时，命中的是姓名 ``张三``，
  高亮的是"张三"这个可视化片段，而不是用户敲的拼音。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..nlp import pinyin as pinyin_util
from ..utils.text import escape_html, unique_keep_order

#: 参与高亮的字段及其中文名（顺序即详情页展示顺序）
FIELD_LABELS: Tuple[Tuple[str, str], ...] = (
    ("name", "姓名"),
    ("cohort", "届别"),
    ("college", "学院"),
    ("major", "专业"),
    ("degree", "学历"),
    ("province", "省份"),
    ("city", "城市"),
    ("destination_org", "单位"),
    ("position", "岗位"),
    ("notice_title", "文章标题"),
)

#: 高亮标记（前端 CSS 里也定义了同名样式）
MARK_OPEN = "<mark>"
MARK_CLOSE = "</mark>"

#: 正文片段默认宽度（字符数）
SNIPPET_WIDTH = 160


def effective_terms(record: Mapping[str, Any], terms: Iterable[str]) -> List[str]:
    """把查询词扩展为"真正的命中文本"。

    例如记录姓名是"张三"、查询词是 ``zhangsan``，则返回 ``["zhangsan", "张三"]``。
    这样高亮才能落在汉字上。
    """
    expanded: List[str] = list(unique_keep_order(terms))
    name = record.get("name")
    for term in list(expanded):
        if pinyin_util.matches(name, term):  # type: ignore[arg-type]
            expanded.extend(pinyin_util.match_spans(name, term))  # type: ignore[arg-type]
    return unique_keep_order(expanded)


def _compile(terms: Sequence[str]) -> Optional[re.Pattern[str]]:
    cleaned = unique_keep_order(terms)
    if not cleaned:
        return None
    cleaned.sort(key=len, reverse=True)
    return re.compile("|".join(re.escape(t) for t in cleaned), re.IGNORECASE)


def mark(text: Optional[str], pattern: Optional[re.Pattern[str]]) -> Tuple[str, bool]:
    """转义 ``text`` 并给命中词加上 ``<mark>``；返回 ``(HTML, 是否有命中)``。"""
    escaped = escape_html(text)
    if not escaped or pattern is None:
        return escaped, False
    hits = 0

    def _wrap(match: "re.Match[str]") -> str:
        nonlocal hits
        hits += 1
        return f"{MARK_OPEN}{match.group(0)}{MARK_CLOSE}"

    return pattern.sub(_wrap, escaped), hits > 0


def snippet(
    raw_text: Optional[str],
    pattern: Optional[re.Pattern[str]],
    *,
    width: int = SNIPPET_WIDTH,
) -> Optional[str]:
    """从正文里截取包含首个命中词的窗口（转义 + 高亮）。

    正文太长不适合整段展示，因此只给"命中位置前后"的一小段；
    没有任何命中时返回 ``None``（前端回退到显示字段区）。
    """
    body = (raw_text or "").strip()
    if not body:
        return None
    if pattern is None:
        return escape_html(body[:width])
    match = pattern.search(body)
    if match is None:
        return None
    half = max(0, (width - len(match.group(0))) // 2)
    start = max(0, match.start() - half)
    end = min(len(body), start + width)
    window = body[start:end]
    html, _ = mark(window, pattern)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{html}{suffix}"


def build(
    record: Mapping[str, Any],
    terms: Sequence[str],
    *,
    extra_terms: Sequence[str] = (),
    snippet_width: int = SNIPPET_WIDTH,
) -> Dict[str, Any]:
    """为一条记录构造高亮信息。

    :param extra_terms: 额外需要高亮的词（如多关键词检索里"命中任意词"的其它词）。
    :return: ``{"fields": {字段: HTML}, "matched_fields": [...], "snippet": str|None,
        "matched_terms": [...]}``
    """
    all_terms = unique_keep_order(list(terms) + list(extra_terms))
    effective = effective_terms(record, all_terms)
    pattern = _compile(effective)

    fields: Dict[str, str] = {}
    matched: List[str] = []
    for field, _label in FIELD_LABELS:
        html, hit = mark(record.get(field), pattern)  # type: ignore[arg-type]
        fields[field] = html
        if hit:
            matched.append(field)

    return {
        "fields": fields,
        "matched_fields": matched,
        "snippet": snippet(record.get("raw_text"), pattern, width=snippet_width),  # type: ignore[arg-type]
        "matched_terms": effective,
    }


__all__ = [
    "FIELD_LABELS",
    "MARK_CLOSE",
    "MARK_OPEN",
    "SNIPPET_WIDTH",
    "build",
    "effective_terms",
    "mark",
    "snippet",
]
