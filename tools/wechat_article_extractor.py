#!/usr/bin/env python3
"""Extract readable text and metadata from a public WeChat article URL.

The tool is intentionally dependency-free so it can run with the system Python:

    python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/..."
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


UA_WECHAT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.50 NetType/WIFI Language/zh_CN"
)

BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tr",
    "ul",
}
SKIP_TAGS = {"script", "style", "svg", "noscript", "canvas"}


@dataclass
class FetchedPage:
    body: str
    final_url: str


class WeChatArticleParser(HTMLParser):
    """Small HTML parser focused on the public WeChat article body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_content = False
        self.content_depth = 0
        self.skip_depth = 0
        self.parts: list[str] = []
        self.images: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self._link_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {key: value or "" for key, value in attrs}
        if tag == "div" and attrs_d.get("id") == "js_content":
            self.in_content = True
            self.content_depth = 1
            self._newline()
            return

        if not self.in_content:
            return

        self.content_depth += 1
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return

        if self.skip_depth:
            return

        if tag in BLOCK_TAGS:
            self._newline()
        elif tag == "br":
            self._newline()
        elif tag in {"td", "th"}:
            self._space()

        if tag == "img":
            src = attrs_d.get("data-src") or attrs_d.get("src") or ""
            if src:
                self.images.append(
                    {
                        "src": normalize_asset_url(src),
                        "alt": attrs_d.get("alt", "").strip(),
                        "data_type": attrs_d.get("data-type", "").strip(),
                    }
                )
        elif tag == "a":
            href = attrs_d.get("href", "").strip()
            self._link_stack.append(href)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_content:
            return

        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1

        if not self.skip_depth and tag in BLOCK_TAGS:
            self._newline()
        if tag == "a" and self._link_stack:
            href = self._link_stack.pop().strip()
            if href:
                self.links.append({"href": normalize_asset_url(href)})

        self.content_depth -= 1
        if self.content_depth <= 0:
            self.in_content = False

    def handle_data(self, data: str) -> None:
        if not self.in_content or self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self._text(text)

    def text(self) -> str:
        value = "".join(self.parts)
        value = value.replace("\u200b", "")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _newline(self) -> None:
        if not self.parts:
            return
        if self.parts[-1].endswith("\n"):
            return
        self.parts.append("\n")

    def _space(self) -> None:
        if self.parts and not self.parts[-1].endswith((" ", "\n")):
            self.parts.append(" ")

    def _text(self, text: str) -> None:
        if self.parts and self.parts[-1] and not self.parts[-1].endswith(("\n", " ")):
            prev = self.parts[-1][-1]
            if prev.isascii() and prev.isalnum() and text[0].isascii() and text[0].isalnum():
                self.parts.append(" ")
        self.parts.append(text)


def normalize_asset_url(value: str) -> str:
    value = html.unescape(value.strip())
    if value.startswith("//"):
        return "https:" + value
    return value


def decode_js_string(value: str | None) -> str | None:
    if value is None:
        return None

    def replace_escape(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith(r"\u"):
            return chr(int(token[2:], 16))
        if token.startswith(r"\x"):
            return chr(int(token[2:], 16))
        return {
            r"\/": "/",
            r"\\": "\\",
            r"\'": "'",
            r'\"': '"',
            r"\n": "\n",
            r"\r": "\r",
            r"\t": "\t",
        }.get(token, token[1:])

    value = re.sub(r"\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|\\[\\/\'\"nrt]", replace_escape, value)
    return html.unescape(value).strip()


def clean_html_text(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def first_match(body: str, patterns: list[str], *, flags: int = re.S) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, body, flags=flags)
        if match:
            return match.group(1)
    return None


def extract_js_var(body: str, name: str) -> str | None:
    pattern = rf"\bvar\s+{re.escape(name)}\s*=\s*(['\"])(.*?)\1"
    match = re.search(pattern, body, flags=re.S)
    if not match:
        return None
    return decode_js_string(match.group(2))


def extract_html_decode_var(body: str, name: str) -> str | None:
    pattern = rf"\bvar\s+{re.escape(name)}\s*=\s*htmlDecode\((['\"])(.*?)\1\)"
    match = re.search(pattern, body, flags=re.S)
    if not match:
        return None
    return decode_js_string(match.group(2))


def fetch_url(url: str, *, timeout: int, cookie: str | None = None, referer: str | None = None) -> FetchedPage:
    quoted_url = urllib.parse.quote(url, safe=":/?&=%._-~#+")
    headers = {
        "User-Agent": UA_WECHAT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    request = urllib.request.Request(quoted_url, headers=headers)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.geturl()
    return FetchedPage(raw.decode(charset, errors="replace"), final_url)


def extract_sogou_inner_url(body: str) -> str | None:
    parts = re.findall(r"url\s*\+=\s*'([^']*)';", body)
    if not parts:
        return None
    url = "".join(parts).replace("&amp;", "&").replace("@", "")
    return url if "mp.weixin.qq.com" in url else None


def parse_publish_time(body: str) -> tuple[int | None, str | None]:
    ct = first_match(body, [r'\bvar\s+ct\s*=\s*"(\d+)"', r"\bct\s*:\s*'(\d+)'"])
    if ct:
        timestamp = int(ct)
        published = dt.datetime.fromtimestamp(timestamp, dt.timezone(dt.timedelta(hours=8)))
        return timestamp, published.isoformat()

    text_time = clean_html_text(
        first_match(
            body,
            [
                r'<em[^>]+id=["\']publish_time["\'][^>]*>(.*?)</em>',
                r'<span[^>]+id=["\']publish_time["\'][^>]*>(.*?)</span>',
            ],
        )
    )
    return None, text_time


def parse_article(body: str, final_url: str) -> dict[str, Any]:
    parser = WeChatArticleParser()
    parser.feed(body)
    publish_ts, publish_time = parse_publish_time(body)

    title = extract_js_var(body, "msg_title") or clean_html_text(
        first_match(
            body,
            [
                r'<h1[^>]+id=["\']activity-name["\'][^>]*>(.*?)</h1>',
                r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']',
            ],
        )
    )
    account = extract_html_decode_var(body, "nickname") or extract_js_var(body, "nickname") or clean_html_text(
        first_match(body, [r'<strong[^>]+id=["\']js_name["\'][^>]*>(.*?)</strong>'])
    )
    author = clean_html_text(
        first_match(
            body,
            [
                r'<span[^>]+id=["\']js_author_name["\'][^>]*>(.*?)</span>',
                r'<em[^>]+id=["\']js_author_name["\'][^>]*>(.*?)</em>',
            ],
        )
    )

    text = parser.text()
    article = {
        "url": final_url,
        "title": title,
        "account": account,
        "author": author,
        "publish_ts": publish_ts,
        "publish_time": publish_time,
        "biz": extract_js_var(body, "biz"),
        "user_name": extract_js_var(body, "user_name"),
        "appmsgid": extract_js_var(body, "appmsgid") or extract_js_var(body, "mid"),
        "idx": extract_js_var(body, "idx"),
        "sn": extract_js_var(body, "sn"),
        "digest": extract_js_var(body, "msg_desc"),
        "source_url": extract_js_var(body, "msg_source_url"),
        "cover": normalize_asset_url(extract_js_var(body, "msg_cdn_url") or ""),
        "text_len": len(text),
        "images": parser.images,
        "links": dedupe_dicts(parser.links, "href"),
        "content_text": text,
    }
    article["content_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    article["warnings"] = detect_warnings(body, article)
    return article


def detect_warnings(body: str, article: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not article.get("content_text"):
        warnings.append("未解析到正文；页面可能需要登录、被限流、已删除，或 DOM 结构发生变化。")
    if "环境异常" in body or "访问频率" in body:
        warnings.append("页面中出现访问异常/频率提示，建议稍后重试或提供 Cookie。")
    if "js_content" not in body:
        warnings.append("HTML 中未出现 js_content 正文容器。")
    return warnings


def dedupe_dicts(items: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        value = item.get(key, "")
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(item)
    return result


def slugify(value: str, fallback: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.U).strip("._")
    value = value[:90].strip("_")
    return value or fallback


def output_stem(article: dict[str, Any]) -> str:
    title = article.get("title") or "wechat_article"
    date_prefix = ""
    if article.get("publish_time") and re.match(r"\d{4}-\d{2}-\d{2}", str(article["publish_time"])):
        date_prefix = str(article["publish_time"])[:10] + "_"
    digest = hashlib.sha1(str(article.get("url") or title).encode("utf-8")).hexdigest()[:8]
    return slugify(f"{date_prefix}{title}_{digest}", f"wechat_article_{digest}")


def article_to_markdown(article: dict[str, Any]) -> str:
    lines = [
        f"# {article.get('title') or '未命名微信文章'}",
        "",
        "## 元数据",
        "",
        f"- 原文链接：{article.get('url') or ''}",
        f"- 公众号：{article.get('account') or ''}",
        f"- 作者：{article.get('author') or ''}",
        f"- 发布时间：{article.get('publish_time') or ''}",
        f"- 正文字数：{article.get('text_len') or 0}",
        f"- 正文 SHA256：`{article.get('content_sha256') or ''}`",
    ]
    if article.get("digest"):
        lines.append(f"- 摘要：{article['digest']}")
    if article.get("source_url"):
        lines.append(f"- 阅读原文：{article['source_url']}")
    if article.get("warnings"):
        lines.extend(["", "## 警告", ""])
        lines.extend(f"- {item}" for item in article["warnings"])

    lines.extend(["", "## 正文", "", article.get("content_text") or ""])

    images = article.get("images") or []
    if images:
        lines.extend(["", "## 图片", ""])
        for image in images:
            alt = image.get("alt") or ""
            lines.append(f"- {image.get('src', '')}" + (f" ({alt})" if alt else ""))

    links = article.get("links") or []
    if links:
        lines.extend(["", "## 链接", ""])
        lines.extend(f"- {item['href']}" for item in links[:100])

    return "\n".join(lines).rstrip() + "\n"


def article_to_text(article: dict[str, Any]) -> str:
    header = [
        article.get("title") or "未命名微信文章",
        f"原文链接：{article.get('url') or ''}",
        f"公众号：{article.get('account') or ''}",
        f"作者：{article.get('author') or ''}",
        f"发布时间：{article.get('publish_time') or ''}",
        "",
    ]
    return "\n".join(header) + (article.get("content_text") or "").rstrip() + "\n"


def write_outputs(article: dict[str, Any], out_dir: Path, fmt: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(article)
    written: list[Path] = []

    if fmt in {"md", "all"}:
        path = out_dir / f"{stem}.md"
        path.write_text(article_to_markdown(article), encoding="utf-8")
        written.append(path)
    if fmt in {"txt", "all"}:
        path = out_dir / f"{stem}.txt"
        path.write_text(article_to_text(article), encoding="utf-8")
        written.append(path)
    if fmt in {"json", "all"}:
        path = out_dir / f"{stem}.json"
        path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)

    return written


def load_article_html(args: argparse.Namespace) -> FetchedPage:
    if args.html_file:
        path = Path(args.html_file)
        return FetchedPage(path.read_text(encoding=args.encoding), args.url or str(path))

    if not args.url:
        raise SystemExit("请提供微信文章 URL，或使用 --html-file 指定本地 HTML。")

    page = fetch_url(args.url, timeout=args.timeout, cookie=args.cookie)
    inner_url = extract_sogou_inner_url(page.body)
    if inner_url:
        return fetch_url(inner_url, timeout=args.timeout, cookie=args.cookie, referer=page.final_url)
    return page


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取公开微信文章正文并保存为 Markdown/TXT/JSON。")
    parser.add_argument("url", nargs="?", help="微信文章 URL，例如 https://mp.weixin.qq.com/s/...")
    parser.add_argument("-o", "--out-dir", default="wechat_article_outputs", help="输出目录。")
    parser.add_argument("-f", "--format", choices=["md", "txt", "json", "all"], default="md", help="输出格式。")
    parser.add_argument("--stdout", action="store_true", help="同时把正文文本打印到终端。")
    parser.add_argument("--save-html", action="store_true", help="同时保存原始 HTML，便于排查解析问题。")
    parser.add_argument("--html-file", help="从本地 HTML 文件解析，不联网。")
    parser.add_argument("--encoding", default="utf-8", help="读取本地 HTML 时使用的编码。")
    parser.add_argument("--cookie", help="可选 Cookie 字符串；遇到访问限制时可从浏览器复制。")
    parser.add_argument("--timeout", type=int, default=30, help="联网超时时间，单位秒。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    page = load_article_html(args)
    article = parse_article(page.body, page.final_url)
    out_dir = Path(args.out_dir)
    written = write_outputs(article, out_dir, args.format)

    if args.save_html:
        out_dir.mkdir(parents=True, exist_ok=True)
        html_path = out_dir / f"{output_stem(article)}.html"
        html_path.write_text(page.body, encoding="utf-8")
        written.append(html_path)

    summary = {
        "title": article.get("title"),
        "account": article.get("account"),
        "publish_time": article.get("publish_time"),
        "text_len": article.get("text_len"),
        "warnings": article.get("warnings"),
        "written": [str(path) for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.stdout:
        print("\n--- 正文 ---\n")
        print(article.get("content_text") or "")

    return 0 if article.get("content_text") else 2


if __name__ == "__main__":
    raise SystemExit(main())
