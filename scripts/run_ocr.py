"""批量 OCR 脚本：对 data/raw/images 下的海报图片执行文字识别。

用法::

    python scripts/run_ocr.py                 # 识别全部图片（已有结果自动跳过）
    python scripts/run_ocr.py --limit 5       # 只跑前 5 张，用于快速验证
    python scripts/run_ocr.py --force         # 忽略已有结果，全部重跑
    python scripts/run_ocr.py --engine rapidocr --no-preprocess

产物写入 ``data/interim/ocr_text/<图片名>.txt`` 与 ``.json``（含文字块坐标与置信度）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ocr.batch import images_dir, iter_images, ocr_output_dir, run_batch  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("run_ocr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量 OCR（RapidOCR / PaddleOCR / Tesseract）")
    parser.add_argument("--dir", type=Path, default=None, help="图片目录，默认 data/raw/images")
    parser.add_argument("--out", type=Path, default=None, help="输出目录，默认 data/interim/ocr_text")
    parser.add_argument("--engine", type=str, default=None, help="指定 OCR 引擎（默认读 config.yaml）")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 张")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 OCR 结果")
    parser.add_argument("--no-preprocess", action="store_true", help="关闭图像预处理（缩放/增强）")
    parser.add_argument("--quiet", action="store_true", help="关闭进度条")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    directory = args.dir.resolve() if args.dir else images_dir()
    out_dir = args.out.resolve() if args.out else ocr_output_dir()

    files = iter_images(directory)
    if not files:
        logger.error("目录中没有图片：%s", directory)
        return 1

    logger.info("开始 OCR：%d 张图片，目录 %s", len(files), directory)
    logger.info("输出目录：%s", out_dir)

    summary = run_batch(
        directory=directory,
        out_dir=out_dir,
        engine_name=args.engine,
        skip_existing=not args.force,
        limit=args.limit,
        preprocess=not args.no_preprocess,
        progress=not args.quiet,
    )

    # 汇总落盘，便于后续构建步骤读取
    report = out_dir / "_ocr_summary.json"
    report.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("汇总已写入 %s", report)

    print(json.dumps(summary.as_dict(), ensure_ascii=False))
    return 0 if summary.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
