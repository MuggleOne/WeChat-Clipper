#!/usr/bin/env python3
"""Undo a WeChat article knowledge-base sync."""

from __future__ import annotations

import argparse
import json
import sys

from knowledge_sync import load_config, undo_from_markdown


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="撤回已同步到 Notion/Obsidian 的微信文章。")
    parser.add_argument("markdown", help="由 wechat_article_extractor.py 生成的 Markdown 文件。")
    parser.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    parser.add_argument("--yes", action="store_true", help="真正执行撤回；不加时只做 dry-run 预览。")
    parser.add_argument("--skip-notion", action="store_true", help="不归档 Notion 页面。")
    parser.add_argument("--skip-obsidian", action="store_true", help="不删除 Obsidian Markdown 和图片目录。")
    parser.add_argument("--all-duplicates", action="store_true", help="归档 Notion 中所有同 URL 页面；默认只归档最新匹配页。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        config = load_config(args.config)
        summary = undo_from_markdown(
            args.markdown,
            config,
            dry_run=not args.yes,
            include_notion=not args.skip_notion,
            include_obsidian=not args.skip_obsidian,
            archive_all_duplicates=args.all_duplicates,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"撤回失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.yes:
        print("dry-run 预览完成；确认无误后加 --yes 才会真正撤回。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
