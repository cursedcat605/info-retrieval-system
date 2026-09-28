"""统一配置加载。

职责：
- 读取 ``config/config.yaml``；
- 读取项目根目录 ``.env`` 并注入环境变量；
- 对配置值中的 ``${VAR}`` 占位符做环境变量插值（未定义时保留原样）。

设计要点：配置只加载一次（缓存），避免各流水线阶段重复读盘。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from .paths import project_root

# ${VAR_NAME} 形式的占位符
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_dotenv(path: Path) -> None:
    """极简 .env 解析：仅支持 ``KEY=VALUE``，已存在的环境变量不覆盖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _interpolate(value: Any) -> Any:
    """递归地把 ``${VAR}`` 替换为环境变量值。"""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


@lru_cache(maxsize=4)
def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """加载并缓存配置字典。"""
    root = project_root()
    _load_dotenv(root / ".env")
    cfg_path = Path(path) if path else root / "config" / "config.yaml"
    if not cfg_path.exists():
        return {}
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _interpolate(data)


def get(keys: Iterable[str], default: Any = None) -> Any:
    """按层级路径取配置，例如 ``get(["ocr", "engine"])``。"""
    node: Any = load_config()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def resolve_path(value: str) -> Path:
    """把配置中的相对路径解析为项目根目录下的绝对路径。"""
    p = Path(value)
    return p if p.is_absolute() else (project_root() / p)


__all__ = ["load_config", "get", "resolve_path", "project_root"]
