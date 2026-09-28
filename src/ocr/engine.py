"""OCR 引擎封装。

对外只暴露两件事：

- :class:`OcrResult` —— 统一的识别结果结构（文本行 + 置信度 + 文本框）；
- :func:`create_engine` —— 按配置构造引擎，并在目标引擎不可用时自动回退。

支持的引擎（均为**懒加载**，import 本模块不会触发模型下载）：

============================  ==========================================
``rapidocr``                  RapidOCR + ONNX Runtime，自带中英文模型，
                              免编译、无需单独安装运行时，推荐默认使用。
``paddleocr``                 PaddleOCR，中文识别效果好，需 paddlepaddle。
``tesseract``                 pytesseract，需系统级 tesseract 可执行文件。
============================  ==========================================
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger("ocr.engine")

# 引擎优先级：目标引擎不可用时，按此顺序尝试回退
_FALLBACK_ORDER = ("rapidocr", "paddleocr", "tesseract")


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class OcrLine:
    """一行识别结果。"""

    text: str
    score: float = 0.0
    box: List[List[float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "score": round(float(self.score), 4), "box": self.box}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OcrLine":
        return cls(
            text=str(data.get("text", "")),
            score=float(data.get("score", 0.0) or 0.0),
            box=list(data.get("box") or []),
        )


@dataclass
class OcrResult:
    """单张图片的完整识别结果。"""

    image: str                                   # 图片文件名（含扩展名）
    engine: str = ""                             # 实际使用的引擎名
    lines: List[OcrLine] = field(default_factory=list)
    elapsed: float = 0.0                         # 识别耗时（秒）
    error: Optional[str] = None                  # 识别失败原因（成功为 None）
    image_path: str = ""                         # 相对项目根的路径，便于溯源

    # -- 便捷视图 ---------------------------------------------------------- #
    @property
    def text(self) -> str:
        """按行拼接的纯文本。"""
        return "\n".join(line.text for line in self.lines)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def mean_score(self) -> float:
        if not self.lines:
            return 0.0
        return float(np.mean([line.score for line in self.lines]))

    # -- 序列化 ------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        return {
            "image": self.image,
            "image_path": self.image_path,
            "engine": self.engine,
            "elapsed": round(self.elapsed, 3),
            "error": self.error,
            "mean_score": round(self.mean_score, 4),
            "lines": [line.to_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OcrResult":
        return cls(
            image=data.get("image", ""),
            image_path=data.get("image_path", ""),
            engine=data.get("engine", ""),
            elapsed=float(data.get("elapsed", 0.0) or 0.0),
            error=data.get("error"),
            lines=[OcrLine.from_dict(d) for d in data.get("lines", [])],
        )


# --------------------------------------------------------------------------- #
# 图像读取（路径安全）
# --------------------------------------------------------------------------- #
def load_image(path: Path | str) -> np.ndarray:
    """读取图片为 BGR ndarray。

    Windows 下 ``cv2.imread`` 无法处理含中文的路径（本项目的图片名与工程路径
    都含中文），因此统一走 ``numpy.frombuffer + cv2.imdecode`` 的方式读盘。
    """
    import cv2

    raw = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法解码图片: {path}")
    return image


def enhance_for_ocr(image: np.ndarray, min_side: int = 1200, max_side: int = 4000) -> np.ndarray:
    """对过小/过大的图片做尺度归一，提升识别率。

    长图（如聊天记录截图）不动短边，只限制最长边，避免内存爆炸。
    """
    import cv2

    h, w = image.shape[:2]
    longest = max(h, w)
    shortest = min(h, w)
    scale = 1.0
    if shortest < min_side:
        scale = min_side / shortest
    if longest * scale > max_side:
        scale = max_side / longest
    if abs(scale - 1.0) < 0.01:
        return image
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


# --------------------------------------------------------------------------- #
# 引擎实现
# --------------------------------------------------------------------------- #
class BaseOcrEngine(ABC):
    """OCR 引擎抽象基类。"""

    name: str = "base"

    @abstractmethod
    def recognize(self, image: np.ndarray) -> List[OcrLine]:
        """对单张图片做文字识别，返回文本行列表。"""


class RapidOcrEngine(BaseOcrEngine):
    """RapidOCR（ONNX Runtime 后端），自带中英文检测 + 识别模型。"""

    name = "rapidocr"

    def __init__(self, **_kwargs: Any) -> None:
        from rapidocr_onnxruntime import RapidOCR  # 懒加载

        self._engine = RapidOCR()

    def recognize(self, image: np.ndarray) -> List[OcrLine]:
        result, _elapse = self._engine(image)
        lines: List[OcrLine] = []
        for item in result or []:
            box, text, score = item[0], item[1], item[2]
            text = str(text).strip()
            if not text:
                continue
            lines.append(OcrLine(text=text, score=float(score), box=[list(map(float, p)) for p in box]))
        return lines


class PaddleOcrEngine(BaseOcrEngine):
    """PaddleOCR 后端。"""

    name = "paddleocr"

    def __init__(
        self,
        lang: str = "ch",
        use_gpu: bool = False,
        det_db_thresh: float = 0.3,
        **_kwargs: Any,
    ) -> None:
        from paddleocr import PaddleOCR  # 懒加载

        self._engine = PaddleOCR(
            use_angle_cls=True,
            lang=lang,
            use_gpu=use_gpu,
            det_db_thresh=det_db_thresh,
            show_log=False,
        )

    def recognize(self, image: np.ndarray) -> List[OcrLine]:
        raw = self._engine.ocr(image, cls=True) or []
        lines: List[OcrLine] = []
        # PaddleOCR 返回 [[ [box, (text, score)], ... ]]
        for page in raw:
            for item in page or []:
                box, (text, score) = item[0], item[1]
                text = str(text).strip()
                if not text:
                    continue
                lines.append(
                    OcrLine(text=text, score=float(score), box=[list(map(float, p)) for p in box])
                )
        return lines


class TesseractOcrEngine(BaseOcrEngine):
    """Tesseract 后端（需系统已安装 tesseract 且含 chi_sim 语言包）。"""

    name = "tesseract"

    def __init__(self, lang: str = "chi_sim+eng", **_kwargs: Any) -> None:
        import pytesseract  # 懒加载

        self._tess = pytesseract
        self._lang = lang

    def recognize(self, image: np.ndarray) -> List[OcrLine]:
        import cv2

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        data = self._tess.image_to_data(
            gray, lang=self._lang, output_type=self._tess.Output.DICT
        )
        lines: List[OcrLine] = []
        for text, conf in zip(data["text"], data["conf"]):
            text = str(text).strip()
            if not text:
                continue
            try:
                score = max(0.0, float(conf)) / 100.0
            except (TypeError, ValueError):
                score = 0.0
            lines.append(OcrLine(text=text, score=score))
        return lines


_REGISTRY: Dict[str, type[BaseOcrEngine]] = {
    RapidOcrEngine.name: RapidOcrEngine,
    PaddleOcrEngine.name: PaddleOcrEngine,
    TesseractOcrEngine.name: TesseractOcrEngine,
}


def available_engines() -> List[str]:
    """返回当前环境真正可用的引擎名列表（会尝试 import 依赖）。"""
    import importlib.util

    probes = {
        "rapidocr": "rapidocr_onnxruntime",
        "paddleocr": "paddleocr",
        "tesseract": "pytesseract",
    }
    return [name for name, mod in probes.items() if importlib.util.find_spec(mod) is not None]


def create_engine(name: Optional[str] = None, **kwargs: Any) -> BaseOcrEngine:
    """构造 OCR 引擎；目标引擎不可用时按 :data:`_FALLBACK_ORDER` 自动回退。"""
    requested = (name or "rapidocr").lower()
    order = [requested] + [n for n in _FALLBACK_ORDER if n != requested]
    last_error: Optional[Exception] = None

    for candidate in order:
        engine_cls = _REGISTRY.get(candidate)
        if engine_cls is None:
            continue
        try:
            engine = engine_cls(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 依赖缺失/模型初始化失败都走回退
            last_error = exc
            logger.warning("OCR 引擎 %s 初始化失败：%s", candidate, exc)
            continue
        if candidate != requested:
            logger.warning("已回退到 OCR 引擎：%s（原请求：%s）", candidate, requested)
        else:
            logger.info("已启用 OCR 引擎：%s", candidate)
        return engine

    raise RuntimeError(
        f"没有任何可用的 OCR 引擎（请求 {requested}）。"
        f"请安装其一：pip install rapidocr-onnxruntime / paddleocr / pytesseract。"
        f"最后错误：{last_error}"
    )


__all__ = [
    "OcrLine",
    "OcrResult",
    "BaseOcrEngine",
    "RapidOcrEngine",
    "PaddleOcrEngine",
    "TesseractOcrEngine",
    "available_engines",
    "create_engine",
    "load_image",
    "enhance_for_ocr",
]
