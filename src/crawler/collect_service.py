"""本地采集服务。

用途：门户接口需要登录会话，而浏览器已登录。于是让浏览器在页面上下文里
调用接口（自动携带会话 Cookie），再把接口返回的 JSON POST 到本服务；
本服务负责解析、下载图片并写入元数据。

跨域说明：浏览器以 ``text/plain`` 发送（简单请求，不触发预检），本服务返回
``Access-Control-Allow-Origin`` 头，浏览器即可读取结果。

启动：
    python scripts/crawl_notices.py --serve --port 8765
浏览器中执行（在已登录的门户页面控制台/Playwright 中）：
    fetch('http://127.0.0.1:8765/collect?keyword=选调分享', {
        method: 'POST', body: JSON.stringify(apiJson)
    });
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..utils.logger import get_logger
from .notice_crawler import NoticeCrawler

LOG = get_logger("collect")


class CollectHandler(BaseHTTPRequestHandler):
    server_version = "NoticeCollect/0.1"

    # ---- 响应助手 ----
    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        # 允许公网页面访问本机（Chrome 私有网络访问策略）
        self.send_header("Access-Control-Allow-Private-Network", "true")

    # ---- 路由 ----
    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/ping":
            self._send_json(200, {"ok": True, "service": "notice-collect"})
        else:
            self._send_json(404, {"ok": False, "error": "use POST /collect"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/collect", "/"):
            self._send_json(404, {"ok": False, "error": "unknown path"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": f"invalid json: {exc}"})
            return

        keyword = payload.get("keyword") or parse_qs(parsed.query).get("keyword", [""])[0]
        api_json = payload.get("data", payload)

        try:
            crawler = NoticeCrawler()
            result = crawler.process_api_payload(api_json, keyword=keyword)
            LOG.info("采集完成：%s", result)
            self._send_json(200, {"ok": True, **result})
        except Exception as exc:  # noqa: BLE001
            LOG.exception("采集失败")
            self._send_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        LOG.info("%s - %s", self.address_string(), fmt % args)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """启动采集服务（阻塞）。"""
    httpd = ThreadingHTTPServer((host, port), CollectHandler)
    LOG.info("采集服务已启动：http://%s:%d/collect （Ctrl+C 停止）", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOG.info("采集服务已停止")
    finally:
        httpd.server_close()
