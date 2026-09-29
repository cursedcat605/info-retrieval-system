"""前端静态资源必须带 ``Cache-Control: no-cache``。

背景：Starlette 的 ``StaticFiles`` 只发 ``ETag`` / ``Last-Modified``、不发
``Cache-Control``，浏览器会按「启发式缓存」把 ``app.js`` 长期压在本地。改完前端
代码后用户刷新页面仍在跑旧脚本，看起来就像「bug 没修好」——本项目就真被这样坑过
一次（搜索框不生效 / 展开更多点了没反应）。这里的断言保证那层保护不会被误删。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from web.backend.app import create_app

client = TestClient(create_app())


def test_html_is_revalidated() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"


def test_scripts_and_styles_are_revalidated() -> None:
    for path in ("/app.js", "/common.js", "/styles.css"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers.get("cache-control") == "no-cache", path


def test_api_responses_are_not_touched() -> None:
    """接口响应不该被这个中间件改动（它们本来就有自己的语义）。"""
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert "cache-control" not in resp.headers
