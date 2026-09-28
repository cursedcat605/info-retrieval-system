"""OCR 版面处理：把乱序的文本框还原为「阅读顺序」。

RapidOCR / PaddleOCR 返回的文本行顺序取决于检测顺序，多栏海报容易出现
「同一行的姓名与学院被拆到相隔很远的位置」。这里按文本框几何信息重建：

1. 以文本框中点为锚，按 y 聚类成「行」（同一行的 y 差值容忍半行高）；
2. 行内按 x 从左到右排序；
3. 行间按 y 从上到下排序。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .engine import OcrLine


@dataclass
class PositionedLine:
    """带几何信息的文本行。"""

    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float
    box: List[List[float]] = None  # type: ignore[assignment]

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)

    @property
    def width(self) -> float:
        return max(1.0, self.right - self.left)


def to_positioned(line: OcrLine) -> Optional[PositionedLine]:
    """把 :class:`OcrLine` 转为几何表示；无坐标信息时返回 ``None``。"""
    box = line.box or []
    if not box:
        return None
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return PositionedLine(
        text=line.text,
        score=line.score,
        left=min(xs),
        top=min(ys),
        right=max(xs),
        bottom=max(ys),
        box=box,
    )


def group_rows(
    lines: Sequence[OcrLine],
    tolerance_ratio: float = 0.6,
) -> List[List[OcrLine]]:
    """把文本行按 y 聚类为「视觉行」。无坐标的行各自单独成行。"""
    positioned: List[PositionedLine] = []
    unpositioned: List[OcrLine] = []
    for line in lines:
        p = to_positioned(line)
        if p is None:
            unpositioned.append(line)
        else:
            positioned.append(p)

    result: List[List[OcrLine]] = []
    if positioned:
        heights = sorted(p.height for p in positioned)
        median_h = heights[len(heights) // 2]
        tol = max(4.0, median_h * tolerance_ratio)

        positioned.sort(key=lambda p: p.center_y)
        current: List[PositionedLine] = [positioned[0]]
        for p in positioned[1:]:
            ref = sum(x.center_y for x in current) / len(current)
            if abs(p.center_y - ref) <= tol:
                current.append(p)
            else:
                result.append(_row_to_lines(current))
                current = [p]
        result.append(_row_to_lines(current))

    for line in unpositioned:
        result.append([line])
    return result


def _row_to_lines(row: Sequence[PositionedLine]) -> List[OcrLine]:
    ordered = sorted(row, key=lambda p: p.left)
    return [OcrLine(text=p.text, score=p.score, box=p.box or []) for p in ordered]


def sort_reading_order(lines: Sequence[OcrLine], tolerance_ratio: float = 0.6) -> List[OcrLine]:
    """返回按阅读顺序（上行→下行，左列→右列）排列的文本行。"""
    ordered: List[OcrLine] = []
    for row in group_rows(lines, tolerance_ratio=tolerance_ratio):
        ordered.extend(row)
    return ordered


def rows_as_text(lines: Sequence[OcrLine], joiner: str = " ", tolerance_ratio: float = 0.6) -> List[str]:
    """把同一视觉行内的多个文本框拼接为一个字符串行。"""
    out: List[str] = []
    for row in group_rows(lines, tolerance_ratio=tolerance_ratio):
        text = joiner.join(line.text for line in row if line.text.strip())
        if text.strip():
            out.append(text.strip())
    return out


def column_split(lines: Sequence[OcrLine], gap_ratio: float = 0.35) -> Tuple[List[OcrLine], List[OcrLine]]:
    """按横向空隙把版面粗略切成左右两栏（用于并排介绍多位主讲人的海报）。

    找不到明显空隙时，右栏返回空列表。
    """
    positioned = [p for p in (to_positioned(line) for line in lines) if p is not None]
    if len(positioned) < 4:
        return list(lines), []

    widths = sorted(p.width for p in positioned)
    typical_w = widths[len(widths) // 2]
    mid = (min(p.left for p in positioned) + max(p.right for p in positioned)) / 2
    left = [line for line, p in zip(lines, positioned) if p.center_x <= mid]
    right = [line for line, p in zip(lines, positioned) if p.center_x > mid]

    # 两侧都要有足够内容，且中缝空隙足够宽，才认定为双栏
    if len(left) < 2 or len(right) < 2:
        return list(lines), []
    left_edge = max(p.right for p in positioned if p.center_x <= mid)
    right_edge = min(p.left for p in positioned if p.center_x > mid)
    if (right_edge - left_edge) < typical_w * gap_ratio:
        return list(lines), []
    return left, right


__all__ = [
    "PositionedLine",
    "to_positioned",
    "group_rows",
    "sort_reading_order",
    "rows_as_text",
    "column_split",
]
