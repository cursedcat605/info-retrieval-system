"""纯文本小工具：拉丁串判定、归一化、HTML 转义。

放在 ``utils`` 而不是 ``nlp``，是为了让 **存储层**（``src/database``）也能
安全使用，而不产生 ``database → nlp`` 的反向依赖。

约定：本模块只做"字符串形态"处理，不做任何语言/语义理解。
"""
from __future__ import annotations

import html
import re
from typing import Iterable, List, Sequence, Tuple

#: 仅由 ASCII 字母、数字、空格、连字符、点、撇号组成的串
_LATIN_RE = re.compile(r"^[A-Za-z0-9\s\-.'’]+$")
#: 归一化时丢弃的字符（保留字母数字）
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def is_latin(text: str) -> bool:
    """``text`` 是否为"可能是拼音"的拉丁输入（不含汉字）。"""
    text = (text or "").strip()
    if not text:
        return False
    if not _LATIN_RE.match(text):
        return False
    # 必须至少含一个字母，纯数字（如 "2024"）不算拼音查询
    return any(ch.isalpha() for ch in text)


def normalize_latin(text: str) -> str:
    """把 ``Zhang San`` / ``zhang-san`` 归一化为 ``zhangsan``。"""
    return _NON_ALNUM_RE.sub("", (text or "").lower())


def escape_html(text: object) -> str:
    """HTML 转义（``None`` → 空串）。"""
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


def first_not_none(*values: object) -> object:
    """返回第一个非 ``None`` 且非空串的值。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def unique_keep_order(items: Iterable[str]) -> List[str]:
    """去重并保持原顺序。"""
    seen: set = set()
    result: List[str] = []
    for item in items:
        key = (item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def fold_digits(text: str) -> Tuple[str, ...]:
    """把文本里的数字串单独抽出来，便于"2024届"与"2024"互相命中。"""
    return tuple(re.findall(r"\d+", text or ""))


def shorten_region(name: str, suffixes: Sequence[str] = ("省", "市", "自治区", "特别行政区")) -> str:
    """``湖北省`` → ``湖北``；``内蒙古自治区`` → ``内蒙古``（仅用于展示）。"""
    text = (name or "").strip()
    for suffix in sorted(suffixes, key=len, reverse=True):
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


__all__ = [
    "escape_html",
    "first_not_none",
    "fold_digits",
    "is_latin",
    "normalize_latin",
    "shorten_region",
    "unique_keep_order",
]
