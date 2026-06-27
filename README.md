# WeChat-Clipper

A lightweight toolkit for collecting, archiving, and analyzing publicly available WeChat Official Account articles.

这个仓库用于管理微信公开文章抓取辅助工具、知识整理脚本，以及已经整理过的学习笔记。

## 当前工具

- `tools/wechat_article_extractor.py`：抓取公开微信文章正文，支持输出 Markdown、TXT、JSON。
- `wechat_etf_research_notes_2026-06-21_2026-06-27/fetch_public_wechat_articles.py`：批量检索并生成 ETF 研究笔记学习摘要。

## 微信文章正文抓取

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/4QC4d_salyqTRjh43NoXhA" -f all -o wechat_article_outputs
```

生成的正文输出默认不纳入 Git 版本管理；需要保存重要内容时，建议整理成自己的笔记后再提交。

## Git 使用

```bash
git status
git add .
git commit -m "描述这次改动"
```

首次连接 GitHub 远端后：

```bash
git push -u origin main
```
