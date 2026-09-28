"""OCR 识别模块：图片 → 文本。"""

from .batch import (
    BatchSummary,
    images_dir,
    iter_images,
    load_image_metadata,
    load_result,
    ocr_output_dir,
    run_batch,
    save_result,
)
from .engine import (
    OcrLine,
    OcrResult,
    available_engines,
    create_engine,
    enhance_for_ocr,
    load_image,
)

__all__ = [
    "OcrLine",
    "OcrResult",
    "available_engines",
    "create_engine",
    "enhance_for_ocr",
    "load_image",
    "BatchSummary",
    "images_dir",
    "iter_images",
    "load_image_metadata",
    "load_result",
    "ocr_output_dir",
    "run_batch",
    "save_result",
]
