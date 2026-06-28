#!/usr/bin/env python3
"""Sync extracted WeChat article Markdown into Notion and Obsidian."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG_NAME = "wechat_clipper_config.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "notion": {
        "database_id": "",
        "data_source_id": "",
        "default_type": "公众号文章",
        "new_status": "未开始",
        "notion_version": "2022-06-28",
        "properties": {
            "title": "主题",
            "url": "网址",
            "author": "作者",
            "type": "类型",
            "status": "状态",
            "added_date": "添加时间",
            "publish_date": "发布日期",
        },
    },
    "obsidian": {
        "vault_path": "~/Obsidian",
        "article_folder": "素材资料/公众号文章",
        "image_folder": "素材资料/图片",
    },
    "wechat_mp": {
        "cookie": "",
        "token": "",
        "page_size": 5,
        "max_pages": 20,
        "request_delay_seconds": 1.0,
    },
    "tracking": {
        "state_path": "wechat_clipper_state.json",
    },
}

MANAGED_START = "WECHAT_CLIPPER_MANAGED_START"
MANAGED_END = "WECHAT_CLIPPER_MANAGED_END"
MAX_RICH_TEXT = 1900
MAX_APPEND_BLOCKS = 100


@dataclass
class ArticleBundle:
    md_path: Path
    json_path: Path | None
    markdown: str
    article: dict[str, Any]
    body_markdown: str
    image_source_map: dict[str, str]


class NotionApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Notion API error {status}: {body}")
        self.status = status
        self.body = body


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base, ensure_ascii=False))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def find_config_path(config_path: str | None = None) -> Path | None:
    if config_path:
        return Path(config_path).expanduser()
    if os.environ.get("WECHAT_CLIPPER_CONFIG"):
        return Path(os.environ["WECHAT_CLIPPER_CONFIG"]).expanduser()

    candidates = [Path.cwd() / LOCAL_CONFIG_NAME, PROJECT_ROOT / LOCAL_CONFIG_NAME]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_config(config_path: str | None = None) -> dict[str, Any]:
    load_dotenv()
    config = DEFAULT_CONFIG
    path = find_config_path(config_path)
    if path and path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        config = deep_merge(config, loaded)
    else:
        config = json.loads(json.dumps(config, ensure_ascii=False))

    env_overrides = {
        ("notion", "database_id"): os.environ.get("NOTION_DATABASE_ID"),
        ("notion", "data_source_id"): os.environ.get("NOTION_DATA_SOURCE_ID"),
        ("notion", "notion_version"): os.environ.get("NOTION_VERSION"),
        ("obsidian", "vault_path"): os.environ.get("OBSIDIAN_VAULT_PATH"),
        ("obsidian", "article_folder"): os.environ.get("OBSIDIAN_ARTICLE_FOLDER"),
        ("obsidian", "image_folder"): os.environ.get("OBSIDIAN_IMAGE_FOLDER"),
        ("wechat_mp", "cookie"): os.environ.get("WECHAT_MP_COOKIE"),
        ("wechat_mp", "token"): os.environ.get("WECHAT_MP_TOKEN"),
        ("tracking", "state_path"): os.environ.get("WECHAT_CLIPPER_STATE_PATH"),
    }
    for path_keys, value in env_overrides.items():
        if usable_env_value(value):
            section, key = path_keys
            config.setdefault(section, {})[key] = value

    return config


def usable_env_value(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip()
    if not normalized or normalized.startswith("your_"):
        return False
    return True


def load_article_bundle(md_path: str | Path) -> ArticleBundle:
    path = Path(md_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Markdown file not found: {path}")

    markdown = path.read_text(encoding="utf-8")
    json_path = path.with_suffix(".json")
    article: dict[str, Any] = {}
    if json_path.exists():
        article = json.loads(json_path.read_text(encoding="utf-8"))

    metadata = parse_markdown_metadata(markdown)
    for key, value in metadata.items():
        article.setdefault(key, value)

    if not article.get("title"):
        article["title"] = metadata.get("title") or path.stem
    if not article.get("url") and metadata.get("url"):
        article["url"] = metadata["url"]
    if not article.get("content_sha256") and metadata.get("content_sha256"):
        article["content_sha256"] = metadata["content_sha256"]

    image_source_map = parse_image_source_map(markdown)
    body_markdown = article.get("content_markdown") or extract_body_section(markdown)

    return ArticleBundle(
        md_path=path,
        json_path=json_path if json_path.exists() else None,
        markdown=markdown,
        article=article,
        body_markdown=body_markdown,
        image_source_map=image_source_map,
    )


def apply_title_suffix(bundle: ArticleBundle, title_suffix: str | None) -> None:
    if not title_suffix:
        return
    suffix = title_suffix.strip()
    if not suffix:
        return
    title = article_title(bundle.article)
    bundle.article["title"] = f"{title} ({suffix})"


def parse_markdown_metadata(markdown: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in markdown.splitlines():
        if line.startswith("# ") and "title" not in metadata:
            metadata["title"] = line[2:].strip()
            continue
        match = re.match(r"^-\s*([^：:]+)[：:]\s*(.*)$", line.strip())
        if not match:
            continue
        key, value = match.groups()
        value = value.strip().strip("`")
        if key == "原文链接":
            metadata["url"] = value
        elif key == "公众号":
            metadata["account"] = value
        elif key == "作者":
            metadata["author"] = value
        elif key == "发布时间":
            metadata["publish_time"] = value
        elif key == "正文 SHA256":
            metadata["content_sha256"] = value
        elif key == "摘要":
            metadata["digest"] = value
    return metadata


def extract_body_section(markdown: str) -> str:
    return extract_named_section(markdown, "正文")


def extract_named_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = index + 1
            break
    if start is None:
        return ""

    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") and body:
            break
        body.append(line)
    return "\n".join(body).strip()


def parse_image_source_map(markdown: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current_local: str | None = None
    for line in markdown.splitlines():
        local_match = re.match(r"^\s*-\s*本地[：:]\s*(.+?)\s*$", line)
        if local_match:
            current_local = html.unescape(local_match.group(1).strip())
            continue
        source_match = re.match(r"^\s*-\s*来源[：:]\s*(https?://.+?)\s*$", line)
        if source_match and current_local:
            result[current_local] = html.unescape(source_match.group(1).strip())
            current_local = None
    return result


def article_title(article: dict[str, Any]) -> str:
    return str(article.get("title") or "未命名微信文章")


def article_url(article: dict[str, Any]) -> str:
    return normalize_source_url(raw_article_url(article))


def raw_article_url(article: dict[str, Any]) -> str:
    return str(article.get("url") or "").strip()


def source_url_candidates(article: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for value in [article_url(article), raw_article_url(article)]:
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def normalize_source_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""

    parsed = urllib.parse.urlsplit(value)
    if parsed.netloc == "mp.weixin.qq.com" and parsed.path.startswith("/s/"):
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def article_author(article: dict[str, Any]) -> str:
    return str(article.get("author") or article.get("account") or "")


def publish_year(article: dict[str, Any]) -> str:
    value = str(article.get("publish_time") or "")
    match = re.match(r"(\d{4})", value)
    return match.group(1) if match else "undated"


def safe_filename(value: str, fallback: str = "wechat_article") -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value).strip(" _")
    value = re.sub(r"\s+", " ", value)
    return value[:120].strip(" _") or fallback


def today_iso() -> str:
    return dt.date.today().isoformat()


def article_publish_date(article: dict[str, Any]) -> str:
    value = str(article.get("publish_time") or "")
    match = re.match(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else ""


def notion_properties(article: dict[str, Any], config: dict[str, Any], *, include_status: bool) -> dict[str, Any]:
    notion_config = config["notion"]
    props = notion_config["properties"]
    result: dict[str, Any] = {
        props["title"]: {"title": text_rich_text(article_title(article))},
        props["author"]: {"rich_text": text_rich_text(article_author(article))},
        props["type"]: {"select": {"name": notion_config.get("default_type") or "公众号文章"}},
        props["added_date"]: {"date": {"start": today_iso()}},
    }
    if article_url(article):
        result[props["url"]] = {"url": article_url(article)}
    publish_date_property = props.get("publish_date")
    publish_date = article_publish_date(article)
    if publish_date_property and publish_date:
        result[publish_date_property] = {"date": {"start": publish_date}}
    if include_status:
        result[props["status"]] = {"status": {"name": notion_config.get("new_status") or "未开始"}}
    return result


def text_rich_text(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    chunks = [text[index : index + MAX_RICH_TEXT] for index in range(0, len(text), MAX_RICH_TEXT)]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def text_block(block_type: str, text: str) -> dict[str, Any]:
    return {"object": "block", "type": block_type, block_type: {"rich_text": text_rich_text(text)}}


def text_blocks(block_type: str, text: str) -> list[dict[str, Any]]:
    if len(text) <= MAX_RICH_TEXT:
        return [text_block(block_type, text)]
    chunks = [text[index : index + MAX_RICH_TEXT] for index in range(0, len(text), MAX_RICH_TEXT)]
    blocks = [text_block(block_type, chunks[0])]
    blocks.extend(text_block("paragraph", chunk) for chunk in chunks[1:])
    return blocks


def image_block(src: str, caption: str = "") -> dict[str, Any] | None:
    if not src.startswith(("http://", "https://")):
        return None
    block: dict[str, Any] = {
        "object": "block",
        "type": "image",
        "image": {"type": "external", "external": {"url": src}},
    }
    if caption:
        block["image"]["caption"] = text_rich_text(caption)
    return block


def notion_image_blocks(src: str, caption: str = "") -> list[dict[str, Any]]:
    block = image_block(src, caption)
    if not block:
        return []
    return [
        {
            "object": "block",
            "type": "column_list",
            "column_list": {
                "children": [
                    {
                        "object": "block",
                        "type": "column",
                        "column": {
                            "width_ratio": 0.3,
                            "children": [block],
                        },
                    },
                    {
                        "object": "block",
                        "type": "column",
                        "column": {
                            "width_ratio": 0.7,
                            "children": [text_block("paragraph", "")],
                        },
                    },
                ]
            },
        }
    ]


def build_managed_blocks(bundle: ArticleBundle) -> list[dict[str, Any]]:
    article = bundle.article
    blocks: list[dict[str, Any]] = [
        text_block("heading_2", "正文"),
    ]
    blocks.extend(markdown_body_to_blocks(bundle))
    blocks.extend(notion_metadata_blocks(article))
    return blocks


def notion_metadata_blocks(article: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        ("原文", article_url(article)),
        ("公众号", article.get("account") or ""),
        ("作者", article.get("author") or ""),
        ("发布时间", article.get("publish_time") or ""),
        ("正文字数", article.get("text_len") or ""),
        ("正文 SHA256", article.get("content_sha256") or ""),
        ("摘要", article.get("digest") or ""),
        ("阅读原文", article.get("source_url") or ""),
    ]
    blocks = [text_block("heading_2", "元数据")]
    blocks.extend(
        text_block("bulleted_list_item", f"{key}：{value}")
        for key, value in rows
        if value not in {"", None}
    )
    return blocks


def markdown_body_to_blocks(bundle: ArticleBundle) -> list[dict[str, Any]]:
    article = bundle.article
    body = bundle.body_markdown or extract_body_section(bundle.markdown)
    images = article.get("images") or []
    marker_to_image = {image.get("marker"): image for image in images if image.get("marker")}

    if marker_to_image:
        blocks: list[dict[str, Any]] = []
        parts = re.split(r"(\{\{WECHAT_IMAGE_\d{4}\}\})", body)
        for part in parts:
            if not part:
                continue
            image = marker_to_image.get(part)
            if image:
                image_blocks = notion_image_blocks(str(image.get("src") or ""), str(image.get("alt") or ""))
                if image_blocks:
                    blocks.extend(image_blocks)
                elif image.get("local_path"):
                    blocks.append(text_block("paragraph", f"图片：{image['local_path']}"))
                if image.get("download_error"):
                    blocks.append(text_block("quote", f"图片下载失败：{image['download_error']}"))
                continue
            blocks.extend(simple_markdown_to_blocks(part, bundle.image_source_map))
        return blocks

    return simple_markdown_to_blocks(body, bundle.image_source_map)


def simple_markdown_to_blocks(markdown: str, image_source_map: dict[str, str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        img_match = re.search(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", stripped, flags=re.I)
        if img_match:
            src = html.unescape(img_match.group(1))
            external_src = image_source_map.get(src) or src
            image_blocks = notion_image_blocks(external_src)
            if image_blocks:
                blocks.extend(image_blocks)
            else:
                blocks.append(text_block("paragraph", f"图片：{src}"))
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            block_type = {1: "heading_1", 2: "heading_2", 3: "heading_3"}[level]
            blocks.extend(text_blocks(block_type, strip_inline_html(heading_match.group(2))))
            continue

        if stripped.startswith("> "):
            blocks.extend(text_blocks("quote", strip_inline_html(stripped[2:])))
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet_match:
            blocks.extend(text_blocks("bulleted_list_item", strip_inline_html(bullet_match.group(1))))
            continue

        if stripped in {"---", "***"}:
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue

        blocks.extend(text_blocks("paragraph", strip_inline_html(stripped)))

    return blocks


def strip_inline_html(text: str) -> str:
    text = re.sub(r"</?p[^>]*>", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def yaml_value(value: Any) -> str:
    if value is None:
        return '""'
    return json.dumps(str(value), ensure_ascii=False)


def strip_existing_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return markdown
    return markdown[end + 5 :].lstrip("\n")


def build_obsidian_markdown(
    bundle: ArticleBundle,
    notion_url: str | None = None,
    *,
    image_rewrites: dict[str, str] | None = None,
    image_dir_rel: str | None = None,
) -> str:
    article = bundle.article
    body = extract_body_section(strip_existing_frontmatter(bundle.markdown)) or bundle.body_markdown
    body = normalize_markdown_source_url(body, article_url(article))
    body = rewrite_image_sources(body, image_rewrites or {})
    body = format_obsidian_body(body)
    links = extract_named_section(bundle.markdown, "链接")

    sections = [
        f"# {article_title(article)}",
        "",
        "## 正文",
        "",
        body.strip(),
        "",
        build_obsidian_metadata(article, notion_url=notion_url, image_dir_rel=image_dir_rel),
    ]
    if links:
        sections.extend(["", "## 链接", "", links.strip()])
    return "\n".join(sections).rstrip() + "\n"


def build_obsidian_metadata(
    article: dict[str, Any],
    *,
    notion_url: str | None,
    image_dir_rel: str | None,
) -> str:
    rows = [
        ("原文链接", article_url(article)),
        ("公众号", article.get("account") or ""),
        ("作者", article.get("author") or ""),
        ("发布时间", article.get("publish_time") or ""),
        ("正文字数", article.get("text_len") or ""),
        ("正文 SHA256", article.get("content_sha256") or ""),
        ("摘要", article.get("digest") or ""),
        ("阅读原文", article.get("source_url") or ""),
        ("Notion 页面", notion_url or ""),
        ("图片目录", image_dir_rel or ""),
        ("来源类型", "wechat_official_account"),
        ("创建工具", "WeChat-Clipper"),
    ]
    lines = ["## 元数据", ""]
    lines.extend(f"- {key}：{value}" for key, value in rows if value not in {"", None})
    return "\n".join(lines)


def rewrite_image_sources(markdown: str, rewrites: dict[str, str]) -> str:
    if not rewrites:
        return markdown

    def replace(match: re.Match[str]) -> str:
        prefix, src, suffix = match.groups()
        src = html.unescape(src)
        return f"{prefix}{rewrites.get(src, src)}{suffix}"

    return re.sub(r"(<img\b[^>]*\bsrc=[\"'])([^\"']+)([\"'][^>]*>)", replace, markdown, flags=re.I)


def format_obsidian_body(markdown: str) -> str:
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(raw_line.rstrip())
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def normalize_markdown_source_url(markdown: str, source_url: str) -> str:
    if not source_url:
        return markdown
    return re.sub(
        r"(^-\s*原文链接[：:]\s*).+$",
        rf"\g<1>{source_url}",
        markdown,
        count=1,
        flags=re.M,
    )


def safe_relative_path(value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def collect_local_image_paths(bundle: ArticleBundle) -> list[Path]:
    rel_values: set[str] = set()
    for image in bundle.article.get("images") or []:
        if image.get("local_path"):
            rel_values.add(str(image["local_path"]))

    for match in re.finditer(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", bundle.markdown, flags=re.I):
        src = html.unescape(match.group(1))
        if not src.startswith(("http://", "https://")):
            rel_values.add(src)

    paths: list[Path] = []
    for value in sorted(rel_values):
        rel = safe_relative_path(value)
        if not rel:
            continue
        source = bundle.md_path.parent / rel
        if source.exists() and source.is_file():
            paths.append(source)
    return paths


def sync_to_obsidian(
    md_path: str | Path,
    config: dict[str, Any],
    *,
    notion_url: str | None = None,
    dry_run: bool = False,
    title_suffix: str | None = None,
) -> dict[str, Any]:
    bundle = load_article_bundle(md_path)
    apply_title_suffix(bundle, title_suffix)
    targets = obsidian_targets(bundle, config)
    dest_md = targets["markdown"]
    image_targets = targets["image_targets"]
    image_dir_rel = targets["image_dir_rel"]
    copied_images: list[str] = []
    if not dry_run:
        dest_md.parent.mkdir(parents=True, exist_ok=True)
        dest_md.write_text(
            build_obsidian_markdown(
                bundle,
                notion_url,
                image_rewrites=targets["image_rewrites"],
                image_dir_rel=image_dir_rel,
            ),
            encoding="utf-8",
        )
        for source, target in image_targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied_images.append(str(target))
    else:
        copied_images = [str(target) for _, target in image_targets]

    return {
        "dry_run": dry_run,
        "article_title": article_title(bundle.article),
        "source_markdown": str(bundle.md_path),
        "obsidian_markdown": str(dest_md),
        "images": copied_images,
    }


def obsidian_targets(bundle: ArticleBundle, config: dict[str, Any]) -> dict[str, Any]:
    vault = Path(config["obsidian"]["vault_path"]).expanduser()
    article_folder = (config["obsidian"].get("article_folder") or "素材资料/公众号文章").strip("/")
    image_folder = config["obsidian"].get("image_folder") or "素材资料/图片"
    md_stem = safe_filename(article_title(bundle.article))
    dest_dir = vault / article_folder / publish_year(bundle.article)
    dest_md = dest_dir / f"{md_stem}.md"
    image_dest_dir = vault / image_folder / md_stem
    image_paths = collect_local_image_paths(bundle)

    image_rewrites: dict[str, str] = {}
    image_targets: list[tuple[Path, Path]] = []
    for source in image_paths:
        old_rel = source.relative_to(bundle.md_path.parent).as_posix()
        target = image_dest_dir / source.name
        new_rel = relative_posix_path(target, dest_dir)
        image_rewrites[old_rel] = new_rel
        image_targets.append((source, target))

    return {
        "vault": vault,
        "markdown": dest_md,
        "image_dir": image_dest_dir,
        "image_targets": image_targets,
        "image_rewrites": image_rewrites,
        "image_dir_rel": relative_posix_path(image_dest_dir, dest_dir) if image_paths else None,
    }


def relative_posix_path(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start)).as_posix()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def undo_from_markdown(
    md_path: str | Path,
    config: dict[str, Any],
    *,
    dry_run: bool = True,
    include_notion: bool = True,
    include_obsidian: bool = True,
    archive_all_duplicates: bool = False,
) -> dict[str, Any]:
    bundle = load_article_bundle(md_path)
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "article_title": article_title(bundle.article),
        "source_markdown": str(bundle.md_path),
    }

    if include_obsidian:
        targets = obsidian_targets(bundle, config)
        vault = targets["vault"]
        markdown_path = targets["markdown"]
        image_dir = targets["image_dir"]
        if not is_within(markdown_path, vault) or not is_within(image_dir, vault):
            raise RuntimeError("撤回目标不在 Obsidian vault 内，已拒绝执行。")

        obsidian_summary = {
            "markdown": str(markdown_path),
            "markdown_exists": markdown_path.exists(),
            "image_dir": str(image_dir),
            "image_dir_exists": image_dir.exists(),
        }
        if not dry_run:
            if markdown_path.exists():
                markdown_path.unlink()
            if image_dir.exists():
                shutil.rmtree(image_dir)
        summary["obsidian"] = obsidian_summary

    if include_notion:
        summary["notion"] = undo_notion_pages(
            bundle.article,
            config,
            dry_run=dry_run,
            archive_all_duplicates=archive_all_duplicates,
        )

    return summary


def undo_notion_pages(
    article: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
    archive_all_duplicates: bool,
) -> dict[str, Any]:
    load_dotenv()
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("缺少 NOTION_TOKEN。请在环境变量或 .env 中配置。")

    client = NotionHttpClient(token, config)
    pages = client.query_existing_pages(source_url_candidates(article))
    selected = pages if archive_all_duplicates else pages[:1]
    archived: list[str] = []
    if not dry_run:
        for page in selected:
            client.archive_page(page["id"])
            archived.append(page["id"])

    return {
        "source_url": article_url(article),
        "matched": [
            {
                "id": page.get("id"),
                "url": page.get("url"),
                "last_edited_time": page.get("last_edited_time"),
            }
            for page in pages
        ],
        "selected": [page.get("id") for page in selected],
        "archived": archived,
        "duplicates_not_selected": max(0, len(pages) - len(selected)),
    }


class NotionHttpClient:
    def __init__(self, token: str, config: dict[str, Any]) -> None:
        self.token = token
        self.config = config
        self.version = config["notion"].get("notion_version") or "2022-06-28"

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = "https://api.notion.com/v1" + path
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Notion-Version", self.version)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise NotionApiError(exc.code, error_body) from exc
        if not raw:
            return {}
        return json.loads(raw)

    def query_existing_pages(self, source_urls: list[str]) -> list[dict[str, Any]]:
        if not source_urls:
            return []

        url_property = self.config["notion"]["properties"]["url"]
        pages_by_id: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for source_url in source_urls:
            body = {
                "filter": {"property": url_property, "url": {"equals": source_url}},
                "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
                "page_size": 10,
            }
            endpoint_succeeded = False
            for endpoint in self.query_endpoints():
                try:
                    result = self.request("POST", endpoint, body)
                    for page in result.get("results") or []:
                        pages_by_id[page["id"]] = page
                    endpoint_succeeded = True
                    break
                except NotionApiError as exc:
                    if exc.status not in {400, 404}:
                        raise
                    errors.append(f"{endpoint}: {exc.body}")
            if not endpoint_succeeded and not pages_by_id:
                continue

        if pages_by_id:
            return sorted(
                pages_by_id.values(),
                key=lambda page: page.get("last_edited_time") or "",
                reverse=True,
            )
        if errors:
            raise RuntimeError("无法查询 Notion 数据库：" + " | ".join(errors))
        return []

    def create_page(self, properties: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        for parent in self.page_parents():
            try:
                return self.request("POST", "/pages", {"parent": parent, "properties": properties})
            except NotionApiError as exc:
                if exc.status not in {400, 404}:
                    raise
                errors.append(f"{parent}: {exc.body}")
        raise RuntimeError("无法创建 Notion 页面：" + " | ".join(errors))

    def update_page_properties(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def archive_page(self, page_id: str) -> dict[str, Any]:
        return self.request("PATCH", f"/pages/{page_id}", {"archived": True})

    def list_children(self, block_id: str) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            params = {"page_size": "100"}
            if start_cursor:
                params["start_cursor"] = start_cursor
            path = f"/blocks/{block_id}/children?{urllib.parse.urlencode(params)}"
            result = self.request("GET", path)
            children.extend(result.get("results") or [])
            if not result.get("has_more"):
                break
            start_cursor = result.get("next_cursor")
        return children

    def delete_block(self, block_id: str) -> None:
        self.request("DELETE", f"/blocks/{block_id}")

    def append_children(self, block_id: str, children: list[dict[str, Any]]) -> None:
        for index in range(0, len(children), MAX_APPEND_BLOCKS):
            chunk = children[index : index + MAX_APPEND_BLOCKS]
            self.request("PATCH", f"/blocks/{block_id}/children", {"children": chunk})

    def replace_managed_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        children = self.list_children(page_id)
        start, end = find_managed_range(children)
        if start is not None:
            delete_until = end if end is not None else len(children) - 1
            for child in children[start : delete_until + 1]:
                self.delete_block(child["id"])
        self.append_children(page_id, blocks)

    def query_endpoints(self) -> list[str]:
        notion = self.config["notion"]
        endpoints: list[str] = []
        data_source_id = notion.get("data_source_id")
        database_id = notion.get("database_id")
        if database_id:
            endpoints.append(f"/databases/{database_id}/query")
        if data_source_id and data_source_id != database_id:
            endpoints.append(f"/databases/{data_source_id}/query")
        if data_source_id:
            endpoints.append(f"/data_sources/{data_source_id}/query")
        return endpoints

    def page_parents(self) -> list[dict[str, str]]:
        notion = self.config["notion"]
        parents: list[dict[str, str]] = []
        if notion.get("database_id"):
            parents.append({"database_id": notion["database_id"]})
        if notion.get("data_source_id"):
            parents.append({"data_source_id": notion["data_source_id"]})
        return parents


def block_plain_text(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    payload = block.get(block_type, {}) if block_type else {}
    rich_text = payload.get("rich_text") or []
    return "".join(item.get("plain_text") or item.get("text", {}).get("content", "") for item in rich_text)


def find_managed_range(children: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    start: int | None = None
    end: int | None = None
    for index, child in enumerate(children):
        text = block_plain_text(child)
        if MANAGED_START in text and start is None:
            start = index
        if MANAGED_END in text and start is not None:
            end = index
            break
    if start is not None:
        return start, end
    return find_structured_managed_range(children)


def find_structured_managed_range(children: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    start: int | None = None
    metadata_index: int | None = None
    for index, child in enumerate(children):
        if child.get("type") == "heading_2" and block_plain_text(child) == "正文":
            start = index
            break
    if start is None:
        return None, None

    for index in range(start + 1, len(children)):
        child = children[index]
        if child.get("type") == "heading_2" and block_plain_text(child) == "元数据":
            metadata_index = index
            break

    if metadata_index is None:
        return start, len(children) - 1

    end = metadata_index
    for index in range(metadata_index + 1, len(children)):
        child = children[index]
        if child.get("type") != "bulleted_list_item":
            break
        end = index
    return start, end


def sync_to_notion(
    md_path: str | Path,
    config: dict[str, Any],
    *,
    dry_run: bool = False,
    force_create: bool = False,
    title_suffix: str | None = None,
) -> dict[str, Any]:
    bundle = load_article_bundle(md_path)
    apply_title_suffix(bundle, title_suffix)
    create_props = notion_properties(bundle.article, config, include_status=True)
    update_props = notion_properties(bundle.article, config, include_status=False)
    blocks = build_managed_blocks(bundle)

    if dry_run:
        return {
            "dry_run": True,
            "action": "force-create-dry-run" if force_create else "dry-run",
            "article_title": article_title(bundle.article),
            "source_url": article_url(bundle.article),
            "properties": create_props,
            "managed_blocks": len(blocks),
        }

    load_dotenv()
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("缺少 NOTION_TOKEN。请在环境变量或 .env 中配置。")

    client = NotionHttpClient(token, config)
    existing_pages = [] if force_create else client.query_existing_pages(source_url_candidates(bundle.article))
    duplicate_count = max(0, len(existing_pages) - 1)
    if existing_pages:
        page = existing_pages[0]
        page_id = page["id"]
        page = client.update_page_properties(page_id, update_props)
        action = "updated"
    else:
        page = client.create_page(create_props)
        page_id = page["id"]
        action = "created_version" if force_create else "created"

    client.replace_managed_blocks(page_id, blocks)
    return {
        "dry_run": False,
        "action": action,
        "article_title": article_title(bundle.article),
        "source_url": article_url(bundle.article),
        "page_id": page_id,
        "page_url": page.get("url"),
        "duplicates": duplicate_count,
        "managed_blocks": len(blocks),
    }
