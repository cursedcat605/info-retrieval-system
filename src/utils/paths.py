"""路径工具：统一解析项目根目录与数据目录。"""
from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """返回项目根目录（本文件位于 <root>/src/utils/paths.py）。"""
    return Path(__file__).resolve().parents[2]


def ensure_dir(path: Path) -> Path:
    """确保目录存在并返回该目录。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir(*parts: str) -> Path:
    """返回 data/ 下的子目录，并按需创建。

    例：data_dir("raw", "images") -> <root>/data/raw/images
    """
    return ensure_dir(project_root().joinpath("data", *parts))
