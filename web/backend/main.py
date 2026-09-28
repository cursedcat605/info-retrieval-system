"""启动入口：``python -m web.backend.main``（等价于 ``python web/backend/main.py``）。

默认监听 ``127.0.0.1:8000``，可在 ``config/config.yaml`` 的 ``web.host`` / ``web.port``
覆盖，或命令行显式指定：

    python -m web.backend.main --host 0.0.0.0 --port 8080 --reload
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许直接 `python web/backend/main.py` 运行（把项目根目录加入 sys.path）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils.config import get  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="选调生去向信息检索系统 · Web 服务")
    parser.add_argument("--host", default=str(get(["web", "host"], "127.0.0.1")))
    parser.add_argument("--port", type=int, default=int(get(["web", "port"], 8000) or 8000))
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    args = parse_args(argv)
    print(f"→ 服务地址：http://{args.host}:{args.port}")
    print(f"→ 接口文档：http://{args.host}:{args.port}/docs")
    uvicorn.run(
        "web.backend.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
