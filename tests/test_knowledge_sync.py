from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from knowledge_sync import (  # noqa: E402
    block_plain_text,
    build_obsidian_markdown,
    build_managed_blocks,
    find_managed_range,
    load_article_bundle,
    normalize_source_url,
    notion_properties,
    source_url_candidates,
    sync_to_notion,
    sync_to_obsidian,
    undo_from_markdown,
)


class KnowledgeSyncTest(unittest.TestCase):
    def make_article_fixture(self, root: Path) -> Path:
        out_dir = root / "wechat_article_outputs"
        image_dir = out_dir / "2026-06-26_sample_images"
        image_dir.mkdir(parents=True)
        (image_dir / "image_01_test.jpg").write_bytes(b"fake image bytes")

        md_path = out_dir / "2026-06-26_sample.md"
        md_path.write_text(
            "\n".join(
                [
                    "# Sample Article",
                    "",
                    "## 元数据",
                    "",
                    "- 原文链接：https://example.com/wechat/sample",
                    "- 公众号：Sample Account",
                    "- 作者：Sample Author",
                    "- 发布时间：2026-06-26T08:00:00+08:00",
                    "- 正文 SHA256：`abc123`",
                    "",
                    "## 正文",
                    "",
                    "Hello before image.",
                    "",
                    '<p align="center"><img src="2026-06-26_sample_images/image_01_test.jpg" alt="image" width="30%"></p>',
                    "",
                    "Hello after image.",
                    "",
                    "## 图片来源",
                    "",
                    "- 本地：2026-06-26_sample_images/image_01_test.jpg",
                    "  - 来源：https://images.example.com/image_01.jpg",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        article = {
            "url": "https://example.com/wechat/sample",
            "title": "Sample Article",
            "account": "Sample Account",
            "author": "Sample Author",
            "publish_time": "2026-06-26T08:00:00+08:00",
            "content_sha256": "abc123",
            "content_markdown": "Hello before image.\n\n{{WECHAT_IMAGE_0001}}\n\nHello after image.",
            "images": [
                {
                    "src": "https://images.example.com/image_01.jpg",
                    "local_path": "2026-06-26_sample_images/image_01_test.jpg",
                    "marker": "{{WECHAT_IMAGE_0001}}",
                    "alt": "image",
                }
            ],
        }
        md_path.with_suffix(".json").write_text(json.dumps(article, ensure_ascii=False), encoding="utf-8")
        return md_path

    def test_notion_blocks_use_external_image_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path = self.make_article_fixture(Path(tmp))
            bundle = load_article_bundle(md_path)
            blocks = build_managed_blocks(bundle)

        image_blocks = [
            column["column"]["children"][0]
            for block in blocks
            if block.get("type") == "column_list"
            for column in block["column_list"]["children"]
            if column["column"].get("children") and column["column"]["children"][0].get("type") == "image"
        ]
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(blocks[0]["type"], "heading_2")
        self.assertEqual(block_plain_text(blocks[0]), "正文")
        self.assertEqual(blocks[-1]["type"], "bulleted_list_item")
        self.assertFalse(any("WECHAT_CLIPPER_MANAGED" in block_plain_text(block) for block in blocks))
        self.assertEqual(
            image_blocks[0]["image"]["external"]["url"],
            "https://images.example.com/image_01.jpg",
        )

    def test_obsidian_sync_copies_markdown_and_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = self.make_article_fixture(root)
            config = {
                "notion": {},
                "obsidian": {
                    "vault_path": str(root / "vault"),
                    "article_folder": "素材资料/公众号文章",
                    "image_folder": "素材资料/图片",
                },
            }
            result = sync_to_obsidian(md_path, config, notion_url="https://notion.example/page")
            dest_md = Path(result["obsidian_markdown"])

            self.assertEqual(dest_md, root / "vault" / "素材资料" / "公众号文章" / "2026" / "Sample Article.md")
            self.assertTrue(dest_md.exists())
            content = dest_md.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# Sample Article\n"))
            self.assertLess(content.index("## 正文"), content.index("## 元数据"))
            self.assertIn("- 原文链接：https://example.com/wechat/sample", content)
            self.assertIn("- Notion 页面：https://notion.example/page", content)
            self.assertIn("- 图片目录：../../图片/Sample Article", content)
            self.assertNotIn("## 图片来源", content)
            self.assertIn('width="30%"', content)
            self.assertIn('src="../../图片/Sample Article/image_01_test.jpg"', content)
            self.assertTrue(
                (
                    root
                    / "vault"
                    / "素材资料"
                    / "图片"
                    / "Sample Article"
                    / "image_01_test.jpg"
                ).exists()
            )

    def test_obsidian_version_suffix_changes_markdown_and_image_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = self.make_article_fixture(root)
            config = {
                "notion": {},
                "obsidian": {
                    "vault_path": str(root / "vault"),
                    "article_folder": "素材资料/公众号文章",
                    "image_folder": "素材资料/图片",
                },
            }
            result = sync_to_obsidian(md_path, config, title_suffix="v2")
            dest_md = Path(result["obsidian_markdown"])

            self.assertEqual(dest_md.name, "Sample Article (v2).md")
            self.assertIn('src="../../图片/Sample Article (v2)/image_01_test.jpg"', dest_md.read_text(encoding="utf-8"))
            self.assertTrue((root / "vault" / "素材资料" / "图片" / "Sample Article (v2)").exists())

    def test_undo_dry_run_reports_obsidian_targets_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = self.make_article_fixture(root)
            config = {
                "notion": {},
                "obsidian": {
                    "vault_path": str(root / "vault"),
                    "article_folder": "素材资料/公众号文章",
                    "image_folder": "素材资料/图片",
                },
            }
            sync_to_obsidian(md_path, config, notion_url="https://notion.example/page")
            summary = undo_from_markdown(
                md_path,
                config,
                dry_run=True,
                include_notion=False,
            )
            markdown = Path(summary["obsidian"]["markdown"])
            image_dir = Path(summary["obsidian"]["image_dir"])

            self.assertTrue(summary["dry_run"])
            self.assertEqual(markdown, root / "vault" / "素材资料" / "公众号文章" / "2026" / "Sample Article.md")
            self.assertTrue(summary["obsidian"]["markdown_exists"])
            self.assertTrue(summary["obsidian"]["image_dir_exists"])
            self.assertTrue(markdown.exists())
            self.assertTrue(image_dir.exists())

    def test_undo_apply_removes_obsidian_markdown_and_image_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = self.make_article_fixture(root)
            config = {
                "notion": {},
                "obsidian": {
                    "vault_path": str(root / "vault"),
                    "article_folder": "素材资料/公众号文章",
                    "image_folder": "素材资料/图片",
                },
            }
            sync_to_obsidian(md_path, config, notion_url="https://notion.example/page")
            summary = undo_from_markdown(
                md_path,
                config,
                dry_run=False,
                include_notion=False,
            )
            markdown = Path(summary["obsidian"]["markdown"])
            image_dir = Path(summary["obsidian"]["image_dir"])

            self.assertFalse(markdown.exists())
            self.assertFalse(image_dir.exists())

    def test_obsidian_markdown_normalizes_metadata_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path = self.make_article_fixture(Path(tmp))
            article = json.loads(md_path.with_suffix(".json").read_text(encoding="utf-8"))
            article["url"] = "https://mp.weixin.qq.com/s/ARTICLE_ID?nwr_flag=1#wechat_redirect"
            md_path.with_suffix(".json").write_text(json.dumps(article, ensure_ascii=False), encoding="utf-8")
            markdown = md_path.read_text(encoding="utf-8").replace(
                "https://example.com/wechat/sample",
                "https://mp.weixin.qq.com/s/ARTICLE_ID?nwr_flag=1#wechat_redirect",
            )
            md_path.write_text(markdown, encoding="utf-8")

            content = build_obsidian_markdown(load_article_bundle(md_path))

        self.assertIn("- 原文链接：https://mp.weixin.qq.com/s/ARTICLE_ID\n", content)
        self.assertNotIn("nwr_flag", content)

    def test_notion_body_keeps_adjacent_lines_as_separate_paragraphs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path = self.make_article_fixture(Path(tmp))
            article = json.loads(md_path.with_suffix(".json").read_text(encoding="utf-8"))
            article["content_markdown"] = "First paragraph.\nSecond paragraph."
            article["images"] = []
            md_path.with_suffix(".json").write_text(json.dumps(article, ensure_ascii=False), encoding="utf-8")
            blocks = build_managed_blocks(load_article_bundle(md_path))

        texts = [block_plain_text(block) for block in blocks if block.get("type") == "paragraph"]
        self.assertIn("First paragraph.", texts)
        self.assertIn("Second paragraph.", texts)
        self.assertNotIn("First paragraph. Second paragraph.", texts)

    def test_structured_notion_range_uses_body_to_metadata_items(self) -> None:
        children = [
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "manual intro"}]}},
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "正文"}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "body"}]}},
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "元数据"}]}},
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "公众号：x"}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "manual after"}]}},
        ]
        self.assertEqual(find_managed_range(children), (1, 4))

    def test_notion_properties_include_status_only_for_create(self) -> None:
        config = {
            "notion": {
                "default_type": "公众号文章",
                "new_status": "未开始",
                "properties": {
                    "title": "主题",
                    "url": "网址",
                    "author": "作者",
                    "type": "类型",
                    "status": "状态",
                    "added_date": "添加时间",
                    "publish_date": "发布日期",
                },
            }
        }
        article = {
            "title": "Sample Article",
            "url": "https://example.com/wechat/sample",
            "author": "",
            "account": "Sample Account",
            "publish_time": "2026-06-26T08:00:00+08:00",
        }

        create_props = notion_properties(article, config, include_status=True)
        update_props = notion_properties(article, config, include_status=False)

        self.assertEqual(create_props["主题"]["title"][0]["text"]["content"], "Sample Article")
        self.assertEqual(create_props["网址"]["url"], "https://example.com/wechat/sample")
        self.assertEqual(create_props["作者"]["rich_text"][0]["text"]["content"], "Sample Account")
        self.assertEqual(create_props["类型"]["select"]["name"], "公众号文章")
        self.assertEqual(create_props["状态"]["status"]["name"], "未开始")
        self.assertEqual(create_props["发布日期"]["date"]["start"], "2026-06-26")
        self.assertNotIn("状态", update_props)

    def test_notion_force_create_dry_run_uses_version_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path = self.make_article_fixture(Path(tmp))
            config = {
                "notion": {
                    "default_type": "公众号文章",
                    "new_status": "未开始",
                    "properties": {
                        "title": "主题",
                        "url": "网址",
                        "author": "作者",
                        "type": "类型",
                        "status": "状态",
                        "added_date": "添加时间",
                        "publish_date": "发布日期",
                    },
                }
            }
            summary = sync_to_notion(md_path, config, dry_run=True, force_create=True, title_suffix="v2")

        self.assertEqual(summary["action"], "force-create-dry-run")
        self.assertEqual(summary["article_title"], "Sample Article (v2)")
        self.assertEqual(summary["properties"]["主题"]["title"][0]["text"]["content"], "Sample Article (v2)")

    def test_wechat_short_url_is_normalized_for_deduplication(self) -> None:
        self.assertEqual(
            normalize_source_url("https://mp.weixin.qq.com/s/ARTICLE_ID?nwr_flag=1#wechat_redirect"),
            "https://mp.weixin.qq.com/s/ARTICLE_ID",
        )

    def test_notion_queries_normalized_and_raw_source_url_candidates(self) -> None:
        self.assertEqual(
            source_url_candidates({"url": "https://mp.weixin.qq.com/s/ARTICLE_ID?nwr_flag=1#wechat_redirect"}),
            [
                "https://mp.weixin.qq.com/s/ARTICLE_ID",
                "https://mp.weixin.qq.com/s/ARTICLE_ID?nwr_flag=1#wechat_redirect",
            ],
        )


if __name__ == "__main__":
    unittest.main()
