# 知识库同步工具

这组脚本负责把 `wechat_article_extractor.py` 生成的 Markdown/JSON 整理到 Notion 和 Obsidian。

## 配置

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

配置 Notion token：

```bash
cp .env.example .env
```

然后在 `.env` 中填写 `NOTION_TOKEN`。真实 `.env` 不会进入 Git。

配置目标库：

```bash
cp config.example.json wechat_clipper_config.json
```

在 `wechat_clipper_config.json` 中填写：

- `notion.database_id` 或 `notion.data_source_id`
- `obsidian.vault_path`
- `obsidian.article_folder`
- `obsidian.image_folder`
- `notion.properties.publish_date`，默认字段名为 `发布日期`
- `wechat_mp.cookie` 和 `wechat_mp.token`，仅在批量导入/更新追踪时需要
- `tracking.state_path`，默认是本地状态文件 `wechat_clipper_state.json`

真实 `wechat_clipper_config.json` 默认被 Git 忽略。

## Markdown 转 Notion

先 dry-run：

```bash
python3 tools/md_to_notion.py wechat_article_outputs/ARTICLE.md --dry-run
```

正式同步：

```bash
python3 tools/md_to_notion.py wechat_article_outputs/ARTICLE.md
```

同步策略：

- 优先读取同名 `.json` sidecar，缺失时从 Markdown 元数据兜底。
- 以 Notion 的 `网址` 字段查重。
- 已存在相同 URL 时更新已有页面，不新建重复页面。
- 新建页面时设置 `状态` 为 `未开始`；更新页面时保留已有 `状态`。
- 正文写入 WeChat-Clipper 托管区域；再次运行只替换托管区域。
- 图片使用原始微信图片外链，本地图片路径保留在同步信息中。
- `发布日期` 使用文章自己的发布时间日期；`添加时间` 仍是同步当天。
- 无标题文本分享页会使用发布日期作为标题，原始文本进入正文。

## Markdown 入库 Obsidian

```bash
python3 tools/sync_obsidian.py wechat_article_outputs/ARTICLE.md
```

同步策略：

- 复制 Markdown 到 `素材资料/公众号文章/<年份>/`，文件名使用文章标题。
- 复制 Markdown 引用到的本地图片到 `素材资料/图片/<Markdown 文件名>/`。
- 重写正文图片路径，让 Markdown 指向新的图片目录。
- 输出结构为标题、正文、元数据；不会保留 `## 图片来源`。
- 不删除 `wechat_article_outputs/` 中的原始采集结果。

## 一键工作流

```bash
python3 tools/wechat_workflow.py run "https://mp.weixin.qq.com/s/ARTICLE_ID"
```

常用选项：

```bash
python3 tools/wechat_workflow.py run "https://mp.weixin.qq.com/s/ARTICLE_ID" --notion-dry-run
python3 tools/wechat_workflow.py run "https://mp.weixin.qq.com/s/ARTICLE_ID" --skip-notion
python3 tools/wechat_workflow.py run "https://mp.weixin.qq.com/s/ARTICLE_ID" --skip-obsidian
```

## 同公众号批量导入

```bash
python3 tools/wechat_workflow.py batch --seed-url "https://mp.weixin.qq.com/s/ARTICLE_ID" --from 2026-06-01 --to 2026-06-30
```

策略：

- 用种子文章识别公众号，再通过本地微信公众平台登录态发现时间段内的文章。
- 按发布时间从旧到新逐篇复用单篇抓取、Notion 同步和 Obsidian 入库。
- 同 URL 且正文 SHA256 未变化时跳过。
- 同 URL 但正文 SHA256 变化时创建新版本副本，标题、Obsidian 文件名和图片目录追加 `v2/v3...`。
- `--dry-run` 只展示发现结果和本地状态判断，不抓取正文、不同步、不写状态。

## 更新追踪

添加订阅：

```bash
python3 tools/wechat_workflow.py track add --name "示例公众号" --seed-url "https://mp.weixin.qq.com/s/ARTICLE_ID" --frequency weekly
```

手动运行：

```bash
python3 tools/wechat_workflow.py track run --frequency weekly
```

查看订阅：

```bash
python3 tools/wechat_workflow.py track list
```

追踪窗口：

- `daily`：默认最近 2 天。
- `weekly`：默认最近 8 天。
- `monthly`：默认最近 35 天。
- 如果上次成功运行更早，会从上次成功日期前 1 天补查。

## 撤回同步

先预览，不会删除或归档：

```bash
python3 tools/wechat_workflow.py undo wechat_article_outputs/ARTICLE.md
```

确认撤回：

```bash
python3 tools/wechat_workflow.py undo wechat_article_outputs/ARTICLE.md --yes
```

撤回策略：

- Notion 页面执行归档，不做永久删除。
- Obsidian 删除已同步的 Markdown 和同名图片目录。
- 不删除 `wechat_article_outputs/` 中的原始采集结果。
- 默认只归档最新匹配的 Notion 页面；如需处理同 URL 重复页，可加 `--all-duplicates`。
