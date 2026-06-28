#!/usr/bin/env python3
"""End-to-end workflow: capture one WeChat article, then sync it to knowledge bases."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from knowledge_sync import (
    article_title,
    article_url,
    load_article_bundle,
    load_config,
    sync_to_notion,
    sync_to_obsidian,
    undo_from_markdown,
)
from wechat_mp_discovery import discover_account_articles, normalized_mp_config, resolve_fakeid
from wechat_tracking import (
    article_record,
    classify_article,
    load_state,
    mark_subscription_run,
    now_iso,
    parse_date,
    record_article_version,
    save_state,
    state_path,
    subscriptions_for,
    tracking_window,
    upsert_subscription,
)
from wechat_article_extractor import (
    download_article_images,
    clean_article_whitespace,
    extract_sogou_inner_url,
    fetch_url,
    output_stem,
    parse_article,
    write_outputs,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="公众号文章采集与知识库同步工作流。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="抓取单篇文章并同步到 Notion/Obsidian。")
    run.add_argument("url", help="微信文章 URL，例如 https://mp.weixin.qq.com/s/ARTICLE_ID")
    run.add_argument("-o", "--out-dir", default="wechat_article_outputs", help="采集输出目录。")
    run.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    run.add_argument("--cookie", help="可选 Cookie 字符串；遇到访问限制时使用。")
    run.add_argument("--timeout", type=int, default=30, help="联网超时时间，单位秒。")
    run.add_argument("--save-html", action="store_true", help="同时保存原始 HTML，便于排查解析问题。")
    run.add_argument("--no-download-images", action="store_true", help="不下载图片到本地。")
    run.add_argument("--skip-notion", action="store_true", help="跳过 Notion 同步。")
    run.add_argument("--skip-obsidian", action="store_true", help="跳过 Obsidian 同步。")
    run.add_argument("--notion-dry-run", action="store_true", help="Notion 只做 dry-run，不写入。")
    run.add_argument("--obsidian-dry-run", action="store_true", help="Obsidian 只做 dry-run，不复制。")

    batch = subparsers.add_parser("batch", help="按公众号发现一段时间内的文章并批量导入。")
    batch.add_argument("--seed-url", required=True, help="该公众号任意一篇文章 URL，用于识别公众号。")
    batch.add_argument("--from", dest="from_date", required=True, help="起始日期，格式 YYYY-MM-DD，包含当天。")
    batch.add_argument("--to", dest="to_date", required=True, help="结束日期，格式 YYYY-MM-DD，包含当天。")
    batch.add_argument("-o", "--out-dir", default="wechat_article_outputs", help="采集输出目录。")
    batch.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    batch.add_argument("--cookie", help="可选文章抓取 Cookie；微信后台发现使用配置里的 wechat_mp.cookie。")
    batch.add_argument("--timeout", type=int, default=30, help="联网超时时间，单位秒。")
    batch.add_argument("--save-html", action="store_true", help="同时保存原始 HTML，便于排查解析问题。")
    batch.add_argument("--no-download-images", action="store_true", help="不下载图片到本地。")
    batch.add_argument("--skip-notion", action="store_true", help="跳过 Notion 同步。")
    batch.add_argument("--skip-obsidian", action="store_true", help="跳过 Obsidian 同步。")
    batch.add_argument("--dry-run", action="store_true", help="只发现文章并预览动作，不抓取正文、不同步、不写状态。")

    track = subparsers.add_parser("track", help="管理和运行公众号更新追踪。")
    track_subparsers = track.add_subparsers(dest="track_command", required=True)

    track_add = track_subparsers.add_parser("add", help="用种子文章添加或更新一个公众号追踪。")
    track_add.add_argument("--name", required=True, help="追踪名称。")
    track_add.add_argument("--seed-url", required=True, help="该公众号任意一篇文章 URL。")
    track_add.add_argument("--frequency", required=True, choices=["daily", "weekly", "monthly"], help="追踪频率。")
    track_add.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    track_add.add_argument("--cookie", help="可选文章抓取 Cookie；微信后台发现使用配置里的 wechat_mp.cookie。")
    track_add.add_argument("--timeout", type=int, default=30, help="联网超时时间，单位秒。")

    track_run = track_subparsers.add_parser("run", help="手动运行日/周/月追踪。")
    track_run.add_argument("--frequency", choices=["daily", "weekly", "monthly"], help="只运行指定频率的追踪。")
    track_run.add_argument("--name", help="只运行指定名称或订阅 ID。")
    track_run.add_argument("-o", "--out-dir", default="wechat_article_outputs", help="采集输出目录。")
    track_run.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    track_run.add_argument("--cookie", help="可选文章抓取 Cookie；微信后台发现使用配置里的 wechat_mp.cookie。")
    track_run.add_argument("--timeout", type=int, default=30, help="联网超时时间，单位秒。")
    track_run.add_argument("--save-html", action="store_true", help="同时保存原始 HTML，便于排查解析问题。")
    track_run.add_argument("--no-download-images", action="store_true", help="不下载图片到本地。")
    track_run.add_argument("--skip-notion", action="store_true", help="跳过 Notion 同步。")
    track_run.add_argument("--skip-obsidian", action="store_true", help="跳过 Obsidian 同步。")
    track_run.add_argument("--dry-run", action="store_true", help="只发现文章并预览动作，不抓取正文、不同步、不写状态。")

    track_list = track_subparsers.add_parser("list", help="列出本地追踪订阅。")
    track_list.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")

    undo = subparsers.add_parser("undo", help="撤回某篇文章的知识库同步结果。")
    undo.add_argument("markdown", help="由 wechat_article_extractor.py 生成的 Markdown 文件。")
    undo.add_argument("--config", help="本地配置文件路径，默认读取 wechat_clipper_config.json。")
    undo.add_argument("--yes", action="store_true", help="真正执行撤回；不加时只做 dry-run 预览。")
    undo.add_argument("--skip-notion", action="store_true", help="不归档 Notion 页面。")
    undo.add_argument("--skip-obsidian", action="store_true", help="不删除 Obsidian Markdown 和图片目录。")
    undo.add_argument("--all-duplicates", action="store_true", help="归档 Notion 中所有同 URL 页面；默认只归档最新匹配页。")
    return parser.parse_args(argv)


def fetch_seed_article_metadata(
    url: str,
    *,
    timeout: int,
    cookie: str | None,
) -> dict[str, Any]:
    page = fetch_url(url, timeout=timeout, cookie=cookie)
    inner_url = extract_sogou_inner_url(page.body)
    if inner_url:
        page = fetch_url(inner_url, timeout=timeout, cookie=cookie, referer=page.final_url)
    article = parse_article(page.body, page.final_url)
    if not article.get("account"):
        raise RuntimeError("种子文章没有解析到公众号名称，无法建立批量导入。")
    return article


def capture_article(
    url: str,
    *,
    out_dir: str,
    timeout: int,
    cookie: str | None,
    download_images: bool,
    save_html: bool,
    allow_empty: bool = False,
) -> dict[str, object]:
    page = fetch_url(url, timeout=timeout, cookie=cookie)
    inner_url = extract_sogou_inner_url(page.body)
    if inner_url:
        page = fetch_url(inner_url, timeout=timeout, cookie=cookie, referer=page.final_url)

    article = parse_article(page.body, page.final_url)
    if is_empty_article(article):
        capture = empty_capture(article)
        if allow_empty:
            return capture
        raise RuntimeError("未解析到正文或发布时间，已跳过，避免写入空文章。")

    output_dir = Path(out_dir)
    stem = output_stem(article)
    image_files: list[Path] = []
    if download_images:
        image_files = download_article_images(
            article,
            output_dir,
            stem,
            timeout=timeout,
            cookie=cookie,
            image_dir=None,
        )

    written = write_outputs(article, output_dir, "all")
    if save_html:
        output_dir.mkdir(parents=True, exist_ok=True)
        html_path = output_dir / f"{stem}.html"
        html_path.write_text(page.body, encoding="utf-8")
        written.append(html_path)

    md_paths = [path for path in written if path.suffix == ".md"]
    if not md_paths:
        raise RuntimeError("采集完成但没有生成 Markdown 文件。")

    return {
        "title": article.get("title"),
        "account": article.get("account"),
        "publish_time": article.get("publish_time"),
        "text_len": article.get("text_len"),
        "images_found": len(article.get("images") or []),
        "images_downloaded": len(image_files),
        "warnings": article.get("warnings"),
        "markdown": str(md_paths[0]),
        "written": [str(path) for path in [*written, *image_files]],
    }


def run_workflow(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    capture = capture_article(
        args.url,
        out_dir=args.out_dir,
        timeout=args.timeout,
        cookie=args.cookie,
        download_images=not args.no_download_images,
        save_html=args.save_html,
    )
    md_path = str(capture["markdown"])

    result: dict[str, object] = {"capture": capture}
    notion_summary: dict[str, object] | None = None
    if not args.skip_notion:
        notion_summary = sync_to_notion(md_path, config, dry_run=args.notion_dry_run)
        result["notion"] = notion_summary

    if not args.skip_obsidian:
        notion_url = None if not notion_summary else str(notion_summary.get("page_url") or "")
        obsidian_summary = sync_to_obsidian(
            md_path,
            config,
            notion_url=notion_url,
            dry_run=args.obsidian_dry_run,
        )
        result["obsidian"] = obsidian_summary

    return result


def batch_workflow(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    state = load_state(config)
    seed_article = fetch_seed_article_metadata(
        args.seed_url,
        timeout=args.timeout,
        cookie=args.cookie,
    )
    from_date = parse_date(args.from_date)
    to_date = parse_date(args.to_date)
    if from_date > to_date:
        raise RuntimeError("--from 不能晚于 --to。")

    fakeid, discovered = discover_account_articles(
        config,
        account_name=str(seed_article.get("account") or ""),
        fakeid=str(seed_article.get("biz") or "") or None,
        from_date=from_date,
        to_date=to_date,
        timeout=args.timeout,
    )
    result = process_discovered_articles(
        discovered,
        config=config,
        state=state,
        account_name=str(seed_article.get("account") or ""),
        out_dir=args.out_dir,
        timeout=args.timeout,
        cookie=args.cookie,
        download_images=not args.no_download_images,
        save_html=args.save_html,
        skip_notion=args.skip_notion,
        skip_obsidian=args.skip_obsidian,
        dry_run=args.dry_run,
    )
    result.update(
        {
            "account": seed_article.get("account"),
            "seed_url": seed_article.get("url"),
            "fakeid": fakeid,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        }
    )
    if not args.dry_run:
        save_state(config, state)
        result["state_path"] = str(state_path(config))
    return result


def process_discovered_articles(
    discovered: list[Any],
    *,
    config: dict[str, Any],
    state: dict[str, Any],
    account_name: str | None = None,
    out_dir: str,
    timeout: int,
    cookie: str | None,
    download_images: bool,
    save_html: bool,
    skip_notion: bool,
    skip_obsidian: bool,
    dry_run: bool,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    counts = {"discovered": len(discovered), "synced": 0, "skipped": 0, "failed": 0}

    for discovered_article in discovered:
        discovered_dict = discovered_article.to_dict()
        url = str(discovered_dict.get("url") or "")
        item_summary: dict[str, Any] = {
            "title": discovered_dict.get("title"),
            "url": url,
            "publish_time": discovered_dict.get("publish_time"),
        }

        if dry_run:
            known = article_record(state, url)
            item_summary["status"] = "known_url_content_not_checked" if known else "would_capture"
            item_summary["latest_version"] = None if not known else known.get("latest_version")
            items.append(item_summary)
            continue

        try:
            capture = capture_article(
                url,
                out_dir=out_dir,
                timeout=timeout,
                cookie=cookie,
                download_images=download_images,
                save_html=save_html,
                allow_empty=True,
            )
            if is_empty_capture(capture):
                capture = capture_text_share_from_discovery(
                    discovered_dict,
                    out_dir=out_dir,
                    account_name=account_name,
                )
            md_path = str(capture["markdown"])
            bundle = load_article_bundle(md_path)
            if not bundle.article.get("content_text") or not bundle.article.get("publish_time"):
                raise RuntimeError("未解析到正文或发布时间，已跳过，避免写入空文章。")
            source_url = article_url(bundle.article)
            decision = classify_article(state, source_url, bundle.article.get("content_sha256"))
            item_summary["capture"] = capture
            item_summary["decision"] = decision

            if decision["action"] == "skip":
                item_summary["status"] = "skipped"
                counts["skipped"] += 1
                items.append(item_summary)
                continue

            notion_summary: dict[str, Any] | None = None
            if not skip_notion:
                notion_summary = sync_to_notion(
                    md_path,
                    config,
                    force_create=bool(decision["force_create"]),
                    title_suffix=decision["title_suffix"],
                )
                item_summary["notion"] = notion_summary

            obsidian_summary: dict[str, Any] | None = None
            if not skip_obsidian:
                notion_url = None if not notion_summary else str(notion_summary.get("page_url") or "")
                obsidian_summary = sync_to_obsidian(
                    md_path,
                    config,
                    notion_url=notion_url,
                    title_suffix=decision["title_suffix"],
                )
                item_summary["obsidian"] = obsidian_summary

            if not (skip_notion and skip_obsidian):
                record_article_version(
                    state,
                    url=source_url,
                    title=article_title(bundle.article),
                    account=bundle.article.get("account"),
                    author=bundle.article.get("author"),
                    publish_time=bundle.article.get("publish_time"),
                    content_sha256=bundle.article.get("content_sha256"),
                    version=int(decision["version"]),
                    source_markdown=md_path,
                    notion_page_url=None if not notion_summary else str(notion_summary.get("page_url") or ""),
                    obsidian_markdown=None
                    if not obsidian_summary
                    else str(obsidian_summary.get("obsidian_markdown") or ""),
                )

            item_summary["status"] = "synced"
            counts["synced"] += 1
        except Exception as exc:  # noqa: BLE001
            item_summary["status"] = "failed"
            item_summary["error"] = str(exc)
            counts["failed"] += 1
        items.append(item_summary)

    return {"dry_run": dry_run, "counts": counts, "items": items}


def track_add_workflow(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    state = load_state(config)
    seed_article = fetch_seed_article_metadata(
        args.seed_url,
        timeout=args.timeout,
        cookie=args.cookie,
    )
    mp_config = normalized_mp_config(config)
    fakeid = str(seed_article.get("biz") or "") or resolve_fakeid(
        mp_config,
        account_name=str(seed_article.get("account") or ""),
        timeout=args.timeout,
    )
    subscription = upsert_subscription(
        state,
        name=args.name,
        frequency=args.frequency,
        seed_url=str(seed_article.get("url") or args.seed_url),
        account=str(seed_article.get("account") or ""),
        biz=seed_article.get("biz"),
        user_name=seed_article.get("user_name"),
        fakeid=fakeid,
    )
    save_state(config, state)
    return {
        "state_path": str(state_path(config)),
        "subscription": subscription,
    }


def track_run_workflow(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    state = load_state(config)
    subscriptions = subscriptions_for(state, frequency=args.frequency, name=args.name)
    summaries: list[dict[str, Any]] = []

    for subscription in subscriptions:
        started_at = now_iso()
        sub_summary: dict[str, Any] = {
            "id": subscription.get("id"),
            "name": subscription.get("name"),
            "frequency": subscription.get("frequency"),
            "started_at": started_at,
        }
        try:
            from_date, to_date = tracking_window(
                str(subscription.get("frequency") or ""),
                str(subscription.get("last_success_at") or ""),
            )
            fakeid, discovered = discover_account_articles(
                config,
                account_name=str(subscription.get("account") or ""),
                fakeid=str(subscription.get("fakeid") or "") or None,
                from_date=from_date,
                to_date=to_date,
                timeout=args.timeout,
            )
            subscription["fakeid"] = fakeid
            sub_summary.update(
                {
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "fakeid": fakeid,
                }
            )
            sub_summary["batch"] = process_discovered_articles(
                discovered,
                config=config,
                state=state,
                account_name=str(subscription.get("account") or ""),
                out_dir=args.out_dir,
                timeout=args.timeout,
                cookie=args.cookie,
                download_images=not args.no_download_images,
                save_html=args.save_html,
                skip_notion=args.skip_notion,
                skip_obsidian=args.skip_obsidian,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                mark_subscription_run(subscription, success=True, started_at=started_at)
        except Exception as exc:  # noqa: BLE001
            sub_summary["error"] = str(exc)
            if not args.dry_run:
                mark_subscription_run(subscription, success=False, started_at=started_at, error=str(exc))
        summaries.append(sub_summary)

    if not args.dry_run:
        save_state(config, state)
    return {
        "dry_run": args.dry_run,
        "state_path": str(state_path(config)),
        "subscriptions": summaries,
    }


def is_empty_capture(capture: dict[str, Any]) -> bool:
    return bool(capture.get("empty")) or not capture.get("text_len") or not capture.get("publish_time")


def is_empty_article(article: dict[str, Any]) -> bool:
    return not article.get("text_len") or not article.get("publish_time")


def empty_capture(article: dict[str, Any]) -> dict[str, object]:
    return {
        "empty": True,
        "title": article.get("title"),
        "account": article.get("account"),
        "publish_time": article.get("publish_time"),
        "text_len": article.get("text_len"),
        "images_found": len(article.get("images") or []),
        "images_downloaded": 0,
        "warnings": article.get("warnings"),
        "markdown": "",
        "written": [],
    }


def capture_text_share_from_discovery(
    discovered: dict[str, Any],
    *,
    out_dir: str,
    account_name: str | None,
) -> dict[str, Any]:
    if str(discovered.get("item_show_type") or "") != "10":
        raise RuntimeError("未解析到正文或发布时间，且不是可回退的文本分享页。")
    content = clean_article_whitespace(str(discovered.get("title") or ""))
    publish_time = str(discovered.get("publish_time") or "")
    if not content or not publish_time:
        raise RuntimeError("文本分享页缺少正文或发布时间，无法生成文章。")

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(str(discovered.get("url") or "")).query)
    publish_date = publish_time[:10] if len(publish_time) >= 10 else "未命名微信文章"
    article = {
        "url": normalize_discovered_url(str(discovered.get("url") or "")),
        "title": publish_date,
        "account": account_name or "",
        "author": "",
        "publish_ts": discovered.get("publish_ts"),
        "publish_time": publish_time,
        "biz": first_query_value(query, "__biz"),
        "user_name": "",
        "appmsgid": first_query_value(query, "mid") or str(discovered.get("appmsgid") or ""),
        "idx": first_query_value(query, "idx"),
        "sn": first_query_value(query, "sn"),
        "digest": discovered.get("digest") or "",
        "source_url": "",
        "cover": discovered.get("cover") or "",
        "item_show_type": "10",
        "text_len": len(content),
        "images": [],
        "links": [],
        "content_text": content,
        "content_markdown": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "warnings": ["正文来自微信后台发现接口的文本分享页 fallback。"],
    }
    written = write_outputs(article, Path(out_dir), "all")
    md_paths = [path for path in written if path.suffix == ".md"]
    return {
        "title": article["title"],
        "account": article["account"],
        "publish_time": article["publish_time"],
        "text_len": article["text_len"],
        "images_found": 0,
        "images_downloaded": 0,
        "warnings": article["warnings"],
        "markdown": str(md_paths[0]),
        "written": [str(path) for path in written],
    }


def first_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""


def normalize_discovered_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "http":
        return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def track_list_workflow(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    state = load_state(config)
    return {
        "state_path": str(state_path(config)),
        "subscriptions": state.get("subscriptions") or [],
    }


def undo_workflow(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    return undo_from_markdown(
        args.markdown,
        config,
        dry_run=not args.yes,
        include_notion=not args.skip_notion,
        include_obsidian=not args.skip_obsidian,
        archive_all_duplicates=args.all_duplicates,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "run":
            summary = run_workflow(args)
        elif args.command == "batch":
            summary = batch_workflow(args)
        elif args.command == "track" and args.track_command == "add":
            summary = track_add_workflow(args)
        elif args.command == "track" and args.track_command == "run":
            summary = track_run_workflow(args)
        elif args.command == "track" and args.track_command == "list":
            summary = track_list_workflow(args)
        elif args.command == "undo":
            summary = undo_workflow(args)
        else:
            raise RuntimeError(f"未知命令：{args.command}")
    except Exception as exc:  # noqa: BLE001
        print(f"工作流执行失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
