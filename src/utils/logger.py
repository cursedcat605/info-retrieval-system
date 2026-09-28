"""日志工具：同时输出到控制台与 logs/ 目录。"""
from __future__ import annotations

import logging
import sys

from .paths import ensure_dir, project_root


def get_logger(name: str = "app", level: int = logging.INFO) -> logging.Logger:
    """获取（并初始化）一个带控制台 + 文件双输出的 logger。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_dir = ensure_dir(project_root() / "logs")
    file_handler = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger
