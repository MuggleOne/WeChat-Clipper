from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from wechat_workflow import capture_article, process_discovered_articles  # noqa: E402


class DiscoveredStub:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data

    def to_dict(self) -> dict[str, object]:
        return self.data


class WeChatWorkflowTest(unittest.TestCase):
    def private_page(self) -> SimpleNamespace:
        return SimpleNamespace(
            body="<html><head><title>private</title></head><body>private</body></html>",
            final_url="https://mp.weixin.qq.com/s/private",
        )

    def test_capture_article_refuses_empty_page_before_writing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("wechat_workflow.fetch_url", return_value=self.private_page()):
                with self.assertRaisesRegex(RuntimeError, "未解析到正文或发布时间"):
                    capture_article(
                        "https://mp.weixin.qq.com/s/private",
                        out_dir=tmp,
                        timeout=1,
                        cookie=None,
                        download_images=True,
                        save_html=True,
                    )

            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_batch_skips_empty_non_text_share_without_local_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            discovered = [
                DiscoveredStub(
                    {
                        "title": "空壳文章",
                        "url": "https://mp.weixin.qq.com/s/private",
                        "publish_time": "2026-04-01T12:00:00+08:00",
                        "item_show_type": 0,
                    }
                )
            ]
            with patch("wechat_workflow.fetch_url", return_value=self.private_page()):
                summary = process_discovered_articles(
                    discovered,
                    config={},
                    state={},
                    account_name="示例公众号",
                    out_dir=tmp,
                    timeout=1,
                    cookie=None,
                    download_images=True,
                    save_html=True,
                    skip_notion=False,
                    skip_obsidian=False,
                    dry_run=False,
                )

            self.assertEqual(summary["counts"]["failed"], 1)
            self.assertEqual(summary["items"][0]["status"], "failed")
            self.assertIn("不是可回退的文本分享页", summary["items"][0]["error"])
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
