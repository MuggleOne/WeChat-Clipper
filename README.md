# WeChat-Clipper

A lightweight toolkit for collecting, archiving, and analyzing publicly available WeChat Official Account articles.

这个仓库用于管理公开微信文章的采集、归档、正文提取、结构化整理和后续知识沉淀工具。

## 项目目标

- 从公开微信文章链接中提取标题、公众号、作者、发布时间、正文、图片链接和文章内链接。
- 将文章保存为适合阅读、检索和二次处理的 Markdown、TXT 或 JSON。
- 兼容无标题文本分享页：标题使用发布日期，原始文本进入正文。
- 为不同主题的公众号文章建立可复用的整理脚本，而不是把流程绑定到单一账号。
- 支持把抓取结果进一步整理成学习笔记、研究素材、索引或知识库条目。

## 当前工具

- `tools/wechat_article_extractor.py`：抓取公开微信文章正文，支持输出 Markdown、TXT、JSON。
- `tools/md_to_notion.py`：将已抓取的 Markdown/JSON 同步到 Notion 数据库。
- `tools/sync_obsidian.py`：将已抓取的 Markdown 和本地图片复制到 Obsidian 仓库。
- `tools/undo_article.py`：撤回已同步到 Notion/Obsidian 的文章。
- `tools/wechat_workflow.py`：串联单篇抓取、批量导入、更新追踪、Notion 同步、Obsidian 入库和撤回。

## 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

Notion 同步需要配置 `NOTION_TOKEN`。可以复制 `.env.example` 为 `.env`，或直接在终端环境变量中配置。

本地配置可以复制 `config.example.json` 为 `wechat_clipper_config.json`。这个文件默认被 Git 忽略，适合放 Notion 数据库 ID、Obsidian 仓库路径等本机配置。

批量导入和更新追踪需要微信公众平台登录态。在本地配置或环境变量中填写 `wechat_mp.cookie` / `WECHAT_MP_COOKIE` 和 `wechat_mp.token` / `WECHAT_MP_TOKEN`。这些值只应保存在本地，不要提交到 Git。

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

## 同步到知识库

先对 Notion 做 dry-run，确认字段和正文块数量：

```bash
python3 tools/md_to_notion.py wechat_article_outputs/ARTICLE.md --dry-run
```

写入或更新 Notion：

```bash
python3 tools/md_to_notion.py wechat_article_outputs/ARTICLE.md
```

复制到 Obsidian：

```bash
python3 tools/sync_obsidian.py wechat_article_outputs/ARTICLE.md
```

一键抓取并同步：

```bash
python3 tools/wechat_workflow.py run "https://mp.weixin.qq.com/s/ARTICLE_ID"
```

按公众号批量导入一段时间内的文章：

```bash
python3 tools/wechat_workflow.py batch --seed-url "https://mp.weixin.qq.com/s/ARTICLE_ID" --from 2026-06-01 --to 2026-06-30
```

批量导入会先用种子文章识别公众号，再用本地微信公众平台登录态发现文章列表。发现结果按发布时间从旧到新处理；已记录且正文 SHA256 未变化的文章会跳过，正文变化的同 URL 文章会创建 `v2/v3...` 版本副本。

添加追踪订阅并手动运行：

```bash
python3 tools/wechat_workflow.py track add --name "示例公众号" --seed-url "https://mp.weixin.qq.com/s/ARTICLE_ID" --frequency weekly
python3 tools/wechat_workflow.py track run --frequency weekly
python3 tools/wechat_workflow.py track list
```

`track run` 不会安装系统定时任务；需要时手动运行即可。`daily` 默认检查最近 2 天，`weekly` 最近 8 天，`monthly` 最近 35 天。如果上次成功运行更早，会从上次成功日期前 1 天补查。

预览批量或追踪，不写 Notion、Obsidian 或状态文件：

```bash
python3 tools/wechat_workflow.py batch --seed-url "https://mp.weixin.qq.com/s/ARTICLE_ID" --from 2026-06-01 --to 2026-06-30 --dry-run
python3 tools/wechat_workflow.py track run --frequency weekly --dry-run
```

预览撤回：

```bash
python3 tools/wechat_workflow.py undo wechat_article_outputs/ARTICLE.md
```

确认撤回：

```bash
python3 tools/wechat_workflow.py undo wechat_article_outputs/ARTICLE.md --yes
```

Notion 会按 `网址` 字段去重；如果已有相同链接的页面，会更新已有页并替换正文和元数据区域，不会删除区域外的手动笔记。Notion 字段会写入 `添加时间` 和文章自己的 `发布日期`。Obsidian 默认把 Markdown 复制到 `素材资料/公众号文章/<年份>/` 并使用文章标题命名，图片复制到 `素材资料/图片/<Markdown 文件名>/`，原始 `wechat_article_outputs/` 保留不动。

## 目录说明

- `tools/`：通用抓取和解析工具。
- `wechat_article_outputs/`：本地生成的正文输出目录，默认被 `.gitignore` 忽略。
- `wechat_clipper_state.json`：批量导入和更新追踪的本地状态文件，默认被 `.gitignore` 忽略。
- `tests/`：脱敏 fixture 和自动化测试。

## Git 使用

```bash
git status
git add .
git commit -m "描述这次改动"
```

```bash
git push
```
