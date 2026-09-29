"""模糊匹配词表：把用户敲的「简写/别称」扩展成库里实际存在的写法。

为什么是独立模块
----------------
「信工」→「信息工程学院」这类映射是**业务知识**，算法推不出来，只能人工维护。
它既不该放在前端（业务词表由前端决定会导致多端不一致），也不该写进数据库
（``data/`` 每次重建都会被覆盖），所以单独放在 ``config/fuzzy_terms.yaml``，
本模块负责读它、在检索前把查询词扩展成「等价词组」。

分工
----
本模块**只做词到词的扩展，不碰 SQL**：扩展出来的词仍交给
:func:`src.search.engine.query` 走常规检索路径（FTS5 / LIKE / 拼音），
因此模糊匹配天然继承了高亮、分面、筛选、排序的全部行为。

规则
----
* 每个用户词扩展成一个**等价词组**（``variants``）：组内是「或」，组间沿用
  检索层原有的「全命中优先，否则放宽为或」规则；
* 命中同一个词的多个分组会**自动合并**（例如「新传」同时挂在学院与专业上），
  所以同一个别名写在多处不会冲突；
* 词表文件 mtime 变了就重新加载 —— 改完 YAML **不需要重启 Web 服务**；
* 文件缺失 / YAML 写坏 / 结构不对：只 WARNING 并退化为「不做扩展」，绝不影响检索。

配置格式（详见文件头注释）::

    groups:
      - canonical: 信息工程学院
        aliases: [信工, 信工院]

    aliases:
      信工: 信息工程学院
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from ..utils.config import get, resolve_path
from ..utils.logger import get_logger
from ..utils.text import unique_keep_order

logger = get_logger("search.fuzzy")

#: 词表默认路径（相对项目根目录）；可用 ``search.fuzzy_terms_path`` 覆盖
DEFAULT_PATH = "config/fuzzy_terms.yaml"

#: 单个词组内最多保留多少个写法（防止手滑写出超长列表把检索拖慢）
MAX_TERMS_PER_GROUP = 20


@dataclass(frozen=True)
class FuzzyGroup:
    """一组等价写法：``canonical`` 是库里真实出现的写法，``terms`` 含全部写法。"""

    canonical: str
    terms: Tuple[str, ...]


#: 加载缓存：文件路径 + mtime 变了才重新解析，避免每次检索都读盘
_cache: Dict[str, Any] = {"key": None, "groups": (), "index": {}, "source": ""}


# --------------------------------------------------------------------------- #
# 归一化与文件定位
# --------------------------------------------------------------------------- #
def normalize(text: Any) -> str:
    """别名比对用的归一化：NFKC（全角转半角）+ 去空白 + 转小写。

    ``Zhang San`` / ``ｚｈａｎｇｓａｎ`` 都会归一成 ``zhangsan``。
    """
    folded = unicodedata.normalize("NFKC", str(text or ""))
    return "".join(folded.split()).lower()


def fuzzy_path() -> Path:
    """词表文件路径（``search.fuzzy_terms_path`` 优先）。"""
    configured = get(["search", "fuzzy_terms_path"], DEFAULT_PATH) or DEFAULT_PATH
    return resolve_path(str(configured))


def _signature(path: Path) -> Optional[Tuple[int, int]]:
    """文件指纹：``(mtime_ns, 字节数)``，``None`` 表示文件不存在。

    手改 YAML 后 mtime 必然变；再加上大小可以兜住 mtime 精度偏粗
    （或同一时刻两次写入）的边角情况，让热重载更可靠。
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


# --------------------------------------------------------------------------- #
# 解析（纯函数，便于单测）
# --------------------------------------------------------------------------- #
def _clean(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def _group(canonical: Any, aliases: Any) -> Optional[FuzzyGroup]:
    """构造一个词组；主词缺失或没有别名时返回 ``None``（并告警）。"""
    main = _clean(canonical)
    if not main:
        logger.warning("模糊匹配词表：有一条规则缺少 canonical（主词），已忽略")
        return None
    terms = unique_keep_order([main] + [t.strip() for t in _as_list(aliases) if t.strip()])
    if len(terms) < 2:
        logger.warning("模糊匹配词表：%s 只有主词、没有别名，已忽略", main)
        return None
    if len(terms) > MAX_TERMS_PER_GROUP:
        logger.warning(
            "模糊匹配词表：%s 的等价写法超过 %d 个，只取前 %d 个",
            main, MAX_TERMS_PER_GROUP, MAX_TERMS_PER_GROUP,
        )
        terms = terms[:MAX_TERMS_PER_GROUP]
    return FuzzyGroup(canonical=main, terms=tuple(terms))


def parse_document(document: Any) -> Tuple[FuzzyGroup, ...]:
    """把 YAML 文档解析为词组列表（容忍多种写法，坏条目只告警）。"""
    if document is None:
        return ()
    if isinstance(document, list):          # 直接给一串词组
        entries: Any = document
        aliases_map: Any = {}
    elif isinstance(document, dict):
        entries = document.get("groups") or []
        aliases_map = document.get("aliases") or {}
    else:
        logger.warning("模糊匹配词表：顶层结构应为「groups 列表」或「别名映射」，已忽略")
        return ()
    if not isinstance(entries, list):
        logger.warning("模糊匹配词表：groups 应为列表，已忽略")
        entries = []

    groups: List[FuzzyGroup] = []
    seen: set = set()
    for entry in entries:
        if isinstance(entry, dict):
            built = _group(
                entry.get("canonical", entry.get("term")),
                entry.get("aliases", entry.get("variants")),
            )
        elif isinstance(entry, (list, tuple)):   # - [主词, 别名1, 别名2]
            built = _group(entry[0] if entry else "", list(entry[1:]))
        else:
            logger.warning("模糊匹配词表：无法识别的规则 %r，已忽略", entry)
            continue
        if built and built.terms not in seen:
            seen.add(built.terms)
            groups.append(built)

    if isinstance(aliases_map, dict):
        for alias, canonical in aliases_map.items():
            if isinstance(canonical, list):      # 别名: [主词A, 主词B]
                for item in canonical:
                    built = _group(item, [alias])
                    if built and built.terms not in seen:
                        seen.add(built.terms)
                        groups.append(built)
                continue
            built = _group(canonical, [alias])
            if built and built.terms not in seen:
                seen.add(built.terms)
                groups.append(built)
    elif aliases_map:
        logger.warning("模糊匹配词表：aliases 应为「别名: 主词」映射，已忽略")

    return tuple(groups)


def _build_index(groups: Sequence[FuzzyGroup]) -> Dict[str, List[Tuple[str, ...]]]:
    """``归一化写法 → 所在词组的全部写法``；同名写法会被合并成多个条目。"""
    index: Dict[str, List[Tuple[str, ...]]] = {}
    for group in groups:
        for term in group.terms:
            index.setdefault(normalize(term), []).append(group.terms)
    return index


# --------------------------------------------------------------------------- #
# 加载与扩展
# --------------------------------------------------------------------------- #
def load_groups(force: bool = False) -> Tuple[FuzzyGroup, ...]:
    """加载词组（带 mtime 缓存；文件缺失时返回空元组并降级为不扩展）。"""
    path = fuzzy_path()
    signature = _signature(path)
    cache_key = (str(path), signature)
    if not force and _cache["key"] == cache_key:
        return _cache["groups"]

    if signature is None:
        logger.debug("模糊匹配词表不存在，本次不做扩展：%s", path)
        groups: Tuple[FuzzyGroup, ...] = ()
    else:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("模糊匹配词表读取失败（%s），本次不做扩展：%s", exc, path)
            groups = ()
        else:
            groups = parse_document(document)

    _cache.update({
        "key": cache_key,
        "groups": groups,
        "index": _build_index(groups),
        "source": str(path),
    })
    if groups:
        logger.debug("模糊匹配词表已加载：%d 组（%s）", len(groups), path)
    return groups


def reset_cache() -> None:
    """清空缓存（测试与热重载用）。"""
    _cache.update({"key": None, "groups": (), "index": {}, "source": ""})


def expand(term: str) -> List[str]:
    """把一个查询词扩展为等价词组；无别名时返回 ``[term]``。

    返回顺序：用户原词在最前，其余按词表顺序，已去重。
    """
    text = str(term or "").strip()
    if not text:
        return []
    load_groups()
    index: Dict[str, List[Tuple[str, ...]]] = _cache["index"]
    merged: List[str] = [text]
    for terms in index.get(normalize(text), ()):   # 命中多个组时自动合并
        merged.extend(terms)
    return unique_keep_order(merged)


def expand_terms(terms: Sequence[str]) -> List[Dict[str, Any]]:
    """批量扩展：返回与 ``terms`` 一一对应的 ``{term, variants, added}``。"""
    load_groups()
    result: List[Dict[str, Any]] = []
    for term in terms:
        variants = expand(term)
        result.append({
            "term": term,
            "variants": variants,
            "added": [v for v in variants if v != term],
        })
    return result


def alias_pairs() -> List[Dict[str, str]]:
    """``[{"alias": "信工", "canonical": "信息工程学院"}]``，供搜索框补全提示。"""
    pairs: List[Dict[str, str]] = []
    for group in load_groups():
        for term in group.terms:
            if term != group.canonical:
                pairs.append({"alias": term, "canonical": group.canonical})
    return pairs


def available() -> bool:
    """词表里是否有可用规则（前端/自检脚本据此说明「模糊匹配是否开启」）。"""
    return bool(load_groups())


def summary() -> Dict[str, Any]:
    """词表概览：路径、词组数、别名数（供 ``/api/config`` 与自检使用）。"""
    groups = load_groups()
    return {
        "path": _cache.get("source", ""),
        "groups": len(groups),
        "aliases": len(alias_pairs()),
        "available": bool(groups),
    }


__all__ = [
    "DEFAULT_PATH",
    "FuzzyGroup",
    "alias_pairs",
    "available",
    "expand",
    "expand_terms",
    "fuzzy_path",
    "load_groups",
    "normalize",
    "parse_document",
    "reset_cache",
    "summary",
]
