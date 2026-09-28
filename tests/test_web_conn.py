# -*- coding: utf-8 -*-
"""Web 层数据库连接的生命周期测试。

背景：``web/backend/app.py`` 用**同步生成器**做 FastAPI 依赖。
FastAPI 会把同步生成器交给 ``contextmanager_in_threadpool``，于是同一次请求里

    conn = connect(...)        # 线程 A（依赖 __enter__）
    ...查询...                  # 线程 B（端点函数体，run_in_threadpool）
    conn.close()               # 线程 C（依赖 __exit__）

三段会落在 anyio 线程池的**不同工作线程**上（串行执行，不并发）。SQLite 默认
``check_same_thread=True``，一旦并发请求让线程池把这三段分派到不同线程，就会抛
``sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in
that same thread``，端点返回 500。

因此 Web 层必须用 ``check_same_thread=False``。这里用临时库复现该线程模型，
避免触碰线上 ``data/db/selects.sqlite``。
"""
from __future__ import annotations

import importlib
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict

from src.database import repo

#: 注意：``web.backend.__init__`` 把 FastAPI 实例也导出为 ``app``，
#: 因此 ``from web.backend import app`` 拿到的是**实例**而不是模块，这里按模块导入。
app_module = importlib.import_module("web.backend.app")


def _in_thread(fn: Callable[[], Any]) -> Dict[str, Any]:
    """在**另一个**线程里执行 ``fn``，返回 ``{"value": ...}`` 或 ``{"error": ...}``。"""
    box: Dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - 测试需要捕获一切以便断言
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    return box


def _temp_db(tmp_path: Path) -> Path:
    """建一个带完整表结构的临时库，模拟线上库。"""
    path = tmp_path / "temp.sqlite"
    conn = repo.init_db(path)
    conn.close()
    return path


def test_sqlite_rejects_cross_thread_reuse_by_default(tmp_path: Path) -> None:
    """默认 ``check_same_thread=True``：换线程用同一连接会报错（说明该限制真实存在）。"""
    conn = repo.connect(_temp_db(tmp_path), create=False)
    try:
        box = _in_thread(lambda: conn.execute("SELECT 1").fetchone()[0])
    finally:
        conn.close()
    assert isinstance(box.get("error"), sqlite3.ProgrammingError), box


def test_connect_allows_cross_thread_when_requested(tmp_path: Path) -> None:
    """``check_same_thread=False``：换线程查询、关闭都不报错。"""
    conn = repo.connect(_temp_db(tmp_path), create=False, check_same_thread=False)
    assert _in_thread(lambda: conn.execute("SELECT 1").fetchone()[0])["value"] == 1
    assert "error" not in _in_thread(conn.close)


def test_get_conn_dependency_is_thread_agnostic(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """复现 FastAPI 线程池模型：建连接 / 用连接 / 关连接分处三个线程。"""
    db = _temp_db(tmp_path)
    # 把 app 里的 connect 换成临时库版本，确保不碰线上库
    monkeypatch.setattr(
        app_module,
        "connect",
        lambda **kwargs: repo.connect(db, **{**kwargs, "create": False}),
    )

    gen = app_module.get_conn()
    conn = next(gen)  # 线程 A：依赖 __enter__ 里建连接
    assert isinstance(conn, sqlite3.Connection)

    box = _in_thread(lambda: conn.execute("SELECT 1").fetchone()[0])
    assert box.get("value") == 1, box  # 线程 B：端点函数体用连接

    box = _in_thread(gen.close)  # 线程 C：依赖 __exit__ 里 conn.close()
    assert "error" not in box, box
