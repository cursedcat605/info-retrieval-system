"""文本清洗与标准化。

OCR 原始输出常见问题及处理：

==============================  ==========================================
全角字符（１２３ＡＢ，。）      统一转半角；中文标点归一为英文标点便于正则
多余空白 / 零宽字符             去除首尾空白、压缩内部空白、剥离不可见字符
页眉页脚 / 水印（微信公众号名）   按规则行过滤
OCR 高频错字                    词典替换（:data:`TYPO_MAP`）
竖排/截断产生的单字噪声行        长度低于阈值直接丢弃
==============================  ==========================================

清洗遵循「**只做减法，不做推断**」原则：不补全、不猜测缺失字段值。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Sequence

# --------------------------------------------------------------------------- #
# 规则表
# --------------------------------------------------------------------------- #
#: OCR 高频错字/粘连修正（仅在完整词级别替换，避免误伤）
TYPO_MAP = {
    "选调牛": "选调生",
    "选调华": "选调生",
    "定向选调牛": "定向选调生",
    "中央民旋大学": "中央民族大学",
    "民旋大学": "民族大学",
    "民旌大学": "民族大学",
    "经验分享佘": "经验分享会",
    "分享佘": "分享会",
    "交流佘": "交流会",
    "座谈佘": "座谈会",
    "沙龙——": "沙龙——",
    "共美沙尤": "共美沙龙",
    "共美沙笼": "共美沙龙",
    "时问": "时间",
    "地占": "地点",
    "主讲八": "主讲人",
    "主办方": "主办方",
    "联系电活": "联系电话",
    "扫码入群": "扫码入群",
}

#: 整行级噪声（正则，忽略大小写）。命中即整行丢弃。
NOISE_LINE_PATTERNS: Sequence[str] = (
    r"^\s*$",
    r"^[\W_]+$",                                   # 纯符号
    r"^[-=_·．\.•—]{2,}$",                          # 分隔线
    r"spacer\.gif",
    r"^(微信号|公众号|微信公众号)\s*[:：]?",
    r"^长按.{0,6}(识别|关注)",
    r"^扫(一)?扫.{0,6}(二维码|关注)",
    r"^\s*\d{1,3}\s*/\s*\d{1,3}\s*$",              # 页码 1/9
    # ---- 海报页脚（宣传信息，非人物字段）----
    r"^扫(一)?扫(关注|参加|报名)?我们?$",
    r"^扫码(关注|参加|报名|入群)?",
    r"^(腾讯会议|会议号|会议ID)",
    r"^关注我们",
    r"^招生就业工作处$",
    r"^(学生)?就业(指导)?(工作)?(处|中心)$",
    r"工作室$",
    r"^等最好的你",
    r"^[\"“”'‘’]?职[\"“”'‘’]?$",
    r"^(主办|承办|协办|指导)(单位|部门)?\s*[:：]?$",
)

#: 页脚标志行：命中后其后的所有行都视为海报页脚（宣传信息），整体截断
FOOTER_LINE_PATTERNS: Sequence[str] = (
    r"^扫\s*码",
    r"^扫一扫",
    r"^腾讯会议",
    r"^关注我们",
    r"^招生就业工作处",
    r"^学生就业指导中心",
    r"工作室\s*$",
    r"^等最好的你",
)
_FOOTER_COMPILED: Sequence["re.Pattern[str]"] = tuple(re.compile(p) for p in FOOTER_LINE_PATTERNS)


def is_footer_line(line: str) -> bool:
    """该行是否为海报页脚标志行（其后内容整体丢弃）。"""
    text = (line or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _FOOTER_COMPILED)

_COMPILED_NOISE = [re.compile(p, re.IGNORECASE) for p in NOISE_LINE_PATTERNS]

#: 中文标点 → 英文标点（便于正则书写，展示时可由前端还原）
_PUNCT_MAP = {
    "，": ",", "。": ".", "、": ",", "；": ";", "：": ":", "？": "?", "！": "!",
    "（": "(", "）": ")", "【": "[", "】": "]", "《": "<", "》": ">",
    "“": '"', "”": '"', "‘": "'", "’": "'", "·": ".", "～": "~", "—": "-", "～": "~",
}

_WHITESPACE_RE = re.compile(r"[\u3000\s]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2028\u2029\ufeff]")


# --------------------------------------------------------------------------- #
# 基础变换
# --------------------------------------------------------------------------- #
def strip_invisible(text: str) -> str:
    """去除零宽字符与控制字符。"""
    return _CONTROL_RE.sub("", text)


def normalize_fullwidth(text: str) -> str:
    """全角 → 半角（含全角字母、数字、空格），随后归一中文标点。"""
    out_chars: List[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:                      # 全角空格
            out_chars.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:          # 全角 ASCII 区
            out_chars.append(chr(code - 0xFEE0))
        else:
            out_chars.append(ch)
    normalized = "".join(out_chars)
    for src, dst in _PUNCT_MAP.items():
        normalized = normalized.replace(src, dst)
    return normalized


def fix_common_typos(text: str) -> str:
    """应用 :data:`TYPO_MAP` 的错字修正。"""
    for wrong, right in TYPO_MAP.items():
        if wrong != right:
            text = text.replace(wrong, right)
    return text


def normalize_text(text: str, fullwidth: bool = True, typos: bool = True) -> str:
    """对单段文本做全部标准化（不含行级过滤）。"""
    text = strip_invisible(text)
    text = unicodedata.normalize("NFKC", text) if fullwidth else text
    if fullwidth:
        text = normalize_fullwidth(text)
    if typos:
        text = fix_common_typos(text)
    return text


# --------------------------------------------------------------------------- #
# 行级清洗
# --------------------------------------------------------------------------- #
def is_noise_line(line: str) -> bool:
    """判断一行是否为噪声（水印、分隔线、纯符号等）。"""
    return any(pattern.search(line) for pattern in _COMPILED_NOISE)


def collapse_repeats(line: str, max_repeat: int = 3) -> str:
    """压缩重复字符，如 ``分享分享分享会`` → ``分享分享会``。

    OCR 在描边字/艺术字上容易产生重复识别，保留少量重复以免破坏正常叠词。
    """
    pattern = re.compile(rf"(.{{1,2}})\1{{{max_repeat},}}")
    return pattern.sub(lambda m: m.group(1) * 2, line)


def clean_lines(
    text: str,
    min_line_length: int = 2,
    remove_whitespace: bool = True,
    normalize: bool = True,
    dedupe: bool = True,
) -> List[str]:
    """把 OCR 原始文本清洗为「干净行列表」。

    :param min_line_length: 短于该长度的行视为截断噪声（中文一个字通常无意义）。
    :param dedupe: 是否去除**完全重复**的行（保留首次出现，用于保留海报区块顺序）。
    """
    lines: List[str] = []
    seen: set[str] = set()

    for raw in text.splitlines():
        line = normalize_text(raw) if normalize else raw
        line = collapse_repeats(line)
        line = _WHITESPACE_RE.sub("" if remove_whitespace else " ", line).strip()
        if not line:
            continue
        # 海报页脚标志行：其后的内容全部视为宣传信息，整体丢弃
        if is_footer_line(line):
            break
        if is_noise_line(line):
            continue
        if len(line) < min_line_length:
            continue
        if dedupe:
            if line in seen:
                continue
            seen.add(line)
        lines.append(line)
    return lines


def clean_text(text: str, **kwargs) -> str:
    """清洗并返回换行拼接的文本。"""
    return "\n".join(clean_lines(text, **kwargs))


def load_stopwords(path: Optional[str] = None) -> set[str]:
    """读取停用词表（可选）。文件不存在时返回空集合。"""
    from pathlib import Path

    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    return {
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


__all__ = [
    "TYPO_MAP",
    "NOISE_LINE_PATTERNS",
    "FOOTER_LINE_PATTERNS",
    "strip_invisible",
    "normalize_fullwidth",
    "fix_common_typos",
    "normalize_text",
    "is_noise_line",
    "is_footer_line",
    "collapse_repeats",
    "clean_lines",
    "clean_text",
    "load_stopwords",
]
