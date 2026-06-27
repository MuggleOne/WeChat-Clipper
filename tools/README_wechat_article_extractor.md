# 微信文章正文抓取工具

脚本位置：

```bash
tools/wechat_article_extractor.py
```

## 常用命令

抓取单篇文章，默认输出 Markdown：

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/ARTICLE_ID"
```

同时输出 Markdown、TXT、JSON：

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/ARTICLE_ID" -f all -o wechat_article_outputs
```

只输出纯文本：

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/ARTICLE_ID" -f txt
```

保存原始 HTML，便于排查解析问题：

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/ARTICLE_ID" --save-html
```

从已经保存的 HTML 离线解析：

```bash
python3 tools/wechat_article_extractor.py --html-file article.html -f all
```

## 输出内容

- `md`：带元数据、正文、图片链接、文章内链接的 Markdown。
- `txt`：标题、元数据和纯正文。
- `json`：结构化字段，包含 `title`、`account`、`author`、`publish_time`、`images`、`links`、`content_text` 等。

## 访问限制

脚本只处理公开可访问的微信文章页。如果遇到空正文、访问异常或频率限制，可以稍后重试，或从浏览器复制 Cookie 后使用：

```bash
python3 tools/wechat_article_extractor.py "文章链接" --cookie "复制到的 Cookie"
```

请仅在有权限的前提下抓取和使用内容，避免高频请求。
