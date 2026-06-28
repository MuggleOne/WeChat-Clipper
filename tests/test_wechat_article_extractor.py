from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from wechat_article_extractor import parse_article  # noqa: E402


class WeChatArticleExtractorTest(unittest.TestCase):
    def test_text_share_article_uses_publish_date_as_title_and_title_as_body(self) -> None:
        body = """
        <html>
          <head><meta property="og:title" content="正文第一段。&#10;&#10;正文第二段。" /></head>
          <body>
            <script>
              window.item_show_type = '10' || '';
              window.biz = '' || 'MzkzMTcwNDcxOQ==';
              window.mid = '' || '' || '2247485165';
              window.idx = '' || '' || '1';
              window.sn = '' || '' || 'sample';
              window.ct = '1780974055' || '';
              window.msg_title = window.title = '正文第一段。\\n\\n正文第二段。' || '';
            </script>
            <script>
              window.cgiDataNew = {
                user_name: 'gh_sample',
                nick_name: '样例公众号'
              };
              var user_name = xml ? getXmlValue('user_name.DATA') : 'gh_sample';
            </script>
          </body>
        </html>
        """

        article = parse_article(body, "https://mp.weixin.qq.com/s/sample")

        self.assertEqual(article["title"], "2026-06-09")
        self.assertEqual(article["content_text"], "正文第一段。\n\n正文第二段。")
        self.assertEqual(article["content_markdown"], "正文第一段。\n\n正文第二段。")
        self.assertEqual(article["account"], "样例公众号")
        self.assertEqual(article["publish_time"], "2026-06-09T11:00:55+08:00")
        self.assertEqual(article["item_show_type"], "10")
        self.assertEqual(article["warnings"], [])


if __name__ == "__main__":
    unittest.main()
