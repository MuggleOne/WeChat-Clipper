#!/usr/bin/env python3
"""Create or update a Notion page from an extracted WeChat Markdown file."""

from __future__ import annotations

import argparse
import json
import sys

from knowledge_sync import load_config, sync_to_notion


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将微信文章 Markdown 同步到 Notion。")
    parser.add_argument("markdown", help="由 wechat_article_extractor.py 生成的 Markdown 文件。")
    parser.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    parser.add_argument("--dry-run", action="store_true", help="只输出将要写入的属性和块数量，不访问 Notion。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        config = load_config(args.config)
        summary = sync_to_notion(args.markdown, config, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"同步 Notion 失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
