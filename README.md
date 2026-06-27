# WeChat-Clipper

A lightweight toolkit for collecting, archiving, and analyzing publicly available WeChat Official Account articles.

这个仓库用于管理公开微信文章的采集、归档、正文提取、结构化整理和后续知识沉淀工具。它不是某一个公众号或某一个主题的专用项目；ETF 研究笔记只是当前仓库里的一个示例场景和历史产物。

## 项目目标

- 从公开微信文章链接中提取标题、公众号、作者、发布时间、正文、图片链接和文章内链接。
- 将文章保存为适合阅读、检索和二次处理的 Markdown、TXT 或 JSON。
- 为不同主题的公众号文章建立可复用的整理脚本，而不是把流程绑定到单一账号。
- 支持把抓取结果进一步整理成学习笔记、研究素材、索引或知识库条目。

## 当前工具

- `tools/wechat_article_extractor.py`：抓取公开微信文章正文，支持输出 Markdown、TXT、JSON。
- `wechat_etf_research_notes_2026-06-21_2026-06-27/fetch_public_wechat_articles.py`：一个面向 ETF 研究笔记的批量检索和摘要示例，可作为后续主题脚本的参考。

## 微信文章正文抓取

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/4QC4d_salyqTRjh43NoXhA" -f all -o wechat_article_outputs
```

生成的正文输出默认不纳入 Git 版本管理；需要保存重要内容时，建议整理成自己的笔记后再提交。

## 目录说明

- `tools/`：通用抓取和解析工具。
- `wechat_etf_research_notes_2026-06-21_2026-06-27/`：一次 ETF 研究笔记整理示例，包含脚本、索引和学习笔记。
- `wechat_article_outputs/`：本地生成的正文输出目录，默认被 `.gitignore` 忽略。

## Git 使用

```bash
git status
git add .
git commit -m "描述这次改动"
```

```bash
git push
```
