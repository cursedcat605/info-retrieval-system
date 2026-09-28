"""Web 后端：把 ``src`` 中已完成的检索能力以 HTTP 接口暴露给前端。

分层原则：

* ``src/search``   负责业务语义（关键词/拼音、筛选、排序、分页、高亮）
* ``web/backend``  只负责 HTTP 适配（参数解析、字段裁剪、静态资源、错误码）

这样接口层保持极薄，检索逻辑全部可被 CLI 与测试直接复用。
"""
from __future__ import annotations

from .app import app, create_app

__all__ = ["app", "create_app"]
