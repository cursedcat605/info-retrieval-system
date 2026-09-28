"""选调经验分享通知图片爬虫 —— 命令行入口。

用法：
  1) 采集服务模式（配合已登录浏览器导出接口数据，推荐）：
       python scripts/crawl_notices.py --serve --port 8765
  2) 离线模式（处理已保存的接口 JSON）：
       python scripts/crawl_notices.py --from-json data/raw/metadata/notice_api_xxx.json --keyword 选调分享
  3) Cookie 自动化模式（自行提供登录 Cookie）：
       python scripts/crawl_notices.py --keyword 选调分享 --cookie-file data/raw/cookies.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler.collect_service import serve  # noqa: E402
from src.crawler.notice_crawler import NoticeCrawler  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

LOG = get_logger("crawl")


def load_cookies(path: Path) -> dict[str, str]:
    """从 JSON 读取 Cookie（支持 {name,value} 列表或 name->value 映射）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "cookies" in data and isinstance(data["cookies"], list):
            data = data["cookies"]
        else:
            return {str(k): str(v) for k, v in data.items()}
    cookies: dict[str, str] = {}
    for item in data:
        if isinstance(item, dict) and "name" in item and "value" in item:
            cookies[item["name"]] = item["value"]
    return cookies


def main() -> int:
    parser = argparse.ArgumentParser(description="选调经验分享通知图片爬虫")
    parser.add_argument("--serve", action="store_true", help="启动本地采集服务")
    parser.add_argument("--host", default="127.0.0.1", help="采集服务监听地址")
    parser.add_argument("--port", type=int, default=8765, help="采集服务端口")
    parser.add_argument("--from-json", type=Path, help="离线模式：接口 JSON 文件路径")
    parser.add_argument("--keyword", default="", help="搜索关键词，用于命名输出")
    parser.add_argument("--cookie-file", type=Path, help="Cookie 自动化模式：Cookie JSON 路径")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args()

    if args.serve:
        serve(args.host, args.port)
        return 0

    crawler = NoticeCrawler()

    if args.from_json:
        payload = json.loads(args.from_json.read_text(encoding="utf-8"))
        result = crawler.process_api_payload(payload, keyword=args.keyword or args.from_json.stem)
        LOG.info("完成：%s", result)
        return 0

    if args.cookie_file:
        cookies = load_cookies(args.cookie_file)
        result = crawler.crawl_from_cookies(
            args.keyword, cookies, page_size=args.page_size, max_pages=args.max_pages
        )
        LOG.info("完成：%s", result)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
