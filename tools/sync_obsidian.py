#!/usr/bin/env python3
"""Copy an extracted WeChat Markdown file and images into an Obsidian vault."""

from __future__ import annotations

import argparse
import json
import sys

from knowledge_sync import load_config, sync_to_obsidian


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将微信文章 Markdown 复制到 Obsidian 仓库。")
    parser.add_argument("markdown", help="由 wechat_article_extractor.py 生成的 Markdown 文件。")
    parser.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    parser.add_argument("--notion-url", help="写入 Obsidian front matter 的 Notion 页面 URL。")
    parser.add_argument("--dry-run", action="store_true", help="只输出目标路径，不复制文件。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        config = load_config(args.config)
        summary = sync_to_obsidian(
            args.markdown,
            config,
            notion_url=args.notion_url,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"同步 Obsidian 失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
