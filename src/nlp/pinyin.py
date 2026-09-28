"""拼音支持：让 ``zhangsan`` 能命中 ``张三``。

检索场景里，用户往往记不住汉字写法或懒得切输入法，直接敲拼音。
本模块提供：

* :func:`variants` —— 返回姓名的 ``(全拼, 声母缩写)``，如 ``("zhangsan", "zs")``；
* :func:`matches` —— 判断一个拉丁查询串是否能命中某个姓名；
* :func:`match_spans` —— 给出命中的字形（用于结果高亮）。

实现要点
--------
* 依赖 ``pypinyin``，**未安装时自动降级**（:func:`available` 返回 ``False``），
  拼音检索静默失效，其余功能不受影响；
* ``lru_cache`` 缓存每个姓名的转换结果，避免重复计算；
* 同时命中"全拼包含"与"声母缩写前缀"两种写法：
  ``zhangsan`` / ``zhangsan`` / ``zs`` / ``zsan``(否) 中前三种可用。
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from ..utils.text import is_latin, normalize_latin

try:  # pragma: no cover - 取决于运行环境是否装了 pypinyin
    from pypinyin import Style, lazy_pinyin

    _PYPI_RUNTIME = True
except Exception:  # pragma: no cover
    Style = None  # type: ignore[assignment]
    lazy_pinyin = None  # type: ignore[assignment]
    _PYPI_RUNTIME = False


def available() -> bool:
    """拼音能力是否可用（``pypinyin`` 是否随环境提供）。"""
    return _PYPI_RUNTIME


@lru_cache(maxsize=4096)
def variants(text: str) -> Tuple[str, str]:
    """返回 ``(全拼, 声母缩写)``；无法转换时返回两个空串。

    >>> variants("张三")
    ('zhangsan', 'zs')
    """
    raw = (text or "").strip()
    if not raw or not _PYPI_RUNTIME:
        return "", ""
    keep = lambda item: list(item)  # noqa: E731 - pypinyin 的 errors 回调
    full = "".join(lazy_pinyin(raw, errors=keep))           # type: ignore[misc]
    initials = "".join(lazy_pinyin(raw, style=Style.FIRST_LETTER, errors=keep))  # type: ignore[misc]
    return normalize_latin(full), normalize_latin(initials)


def matches(name: Optional[str], query: str) -> bool:
    """拉丁查询串 ``query`` 是否能命中姓名 ``name``。"""
    if not name or not is_latin(query):
        return False
    full, initials = variants(name)
    if not full:
        return False
    query = normalize_latin(query)
    if not query:
        return False
    if len(query) <= 2:
        # 1~2 个字母：只认声母前缀，避免 "zs" 命中 "zhangsan" 之外的噪声
        return initials.startswith(query)
    return query in full or initials.startswith(query)


def match_spans(name: Optional[str], query: str) -> List[str]:
    """返回该查询在姓名上的"可视命中片段"，用于高亮。

    拼音查询命中时，整段姓名都会被高亮（拼音无法对应到具体某个字）。
    """
    if matches(name, query):
        return [str(name)]
    return []


def expand_terms(name: Optional[str], terms: Sequence[str]) -> Tuple[str, ...]:
    """把查询词里所有能通过拼音命中 ``name`` 的词换成真实姓名。"""
    expanded: List[str] = []
    for term in terms:
        if is_latin(term) and matches(name, term):
            expanded.append(str(name))
    return tuple(expanded)


__all__ = ["available", "expand_terms", "match_spans", "matches", "variants"]
