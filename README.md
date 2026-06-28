# WeChat-Clipper

A lightweight toolkit for collecting, archiving, and analyzing publicly available WeChat Official Account articles.

这个仓库用于管理公开微信文章的采集、归档、正文提取、结构化整理和后续知识沉淀工具。

## 项目目标

- 从公开微信文章链接中提取标题、公众号、作者、发布时间、正文、图片链接和文章内链接。
- 将文章保存为适合阅读、检索和二次处理的 Markdown、TXT 或 JSON。
- 为不同主题的公众号文章建立可复用的整理脚本，而不是把流程绑定到单一账号。
- 支持把抓取结果进一步整理成学习笔记、研究素材、索引或知识库条目。

## 当前工具

- `tools/wechat_article_extractor.py`：抓取公开微信文章正文，支持输出 Markdown、TXT、JSON。

## 微信文章正文抓取

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/ARTICLE_ID" -f all -o wechat_article_outputs
```

连正文图片一起保存到本地：

```bash
python3 tools/wechat_article_extractor.py "https://mp.weixin.qq.com/s/ARTICLE_ID" -f all -o wechat_article_outputs --download-images
```

生成的 Markdown 会把图片插回正文中的原始位置，并居中按 30% 宽度显示。

生成的正文输出默认不纳入 Git 版本管理；需要保存重要内容时，建议整理成自己的笔记后再提交。

## 目录说明

- `tools/`：通用抓取和解析工具。
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
