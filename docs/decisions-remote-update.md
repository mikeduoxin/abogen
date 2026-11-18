# 远程更新

## 已确定

### 1. 拷打强度：细问

结论：按细问继续，检查目标、约束、取舍和默认假设。

理由：用户选择强度 3。仓库事实已经对齐，后面不再重复「远程有没有更新」。

### 2. 保留决策记录

结论：每定一条就追加到本文件，格式为「结论 + 理由」，并维护文末的待定问题。

理由：用户要求留下。后续接上游时需要能回看当时为什么这么选。

### 3. 方向：把本地接到上游最新架构

结论：目标是把本地接到 `denizsafak/abogen` 的最新 `main`，而不是只补 fork 上缺的功能，也不是只从上游挑修复、留在旧结构上。

理由：用户选择该方向。上游比正在跟踪的 fork `mikeduoxin/abogen` 快进 256 个提交（fork 尖端 2026-04-30，上游尖端 2026-11-01），其中包含 TTS 插件架构和 `domain` 层。fork 上的提交上游都有。

### 4. 移植范围只包括 Voicebox

结论：接到上游时只迁 Voicebox 功能。Windows 启动脚本、中文文档、GPU 测试脚本留在安全分支里，不作为移植内容。

理由：用户选择范围 1。WebUI、书籍解析、Supertonic 已在上游祖先提交中。Voicebox 功能包括合成适配器，以及它在音色配置、WebUI 导入和转换流程里的接入，不只是单文件 `abogen/tts_voicebox.py`。

### 5. 语音标记和单词替换跟随上游

结论：接到上游后保留 `<<VOICE:...>>` 和单词替换，不从上游实现里删掉。

理由：用户记不清工作区里缺少这两项是不是有意删除，选择先按上游保留。工作区在这两组功能上没有独立改动。

### 6. 先做安全分支

结论：把当时的工作区提交到安全分支，作为移植前的存档。

理由：用户选择先存档。工作区尚未提交时，一旦切到上游尖端，未提交的 Voicebox 最容易丢。安全分支保存全部本地文件；「只迁 Voicebox」约束的是移植内容，不是那次存档。

已执行：分支 `safety/local-worktree-2026-11-18`，提交 `7108b66` 与 `4700167`。没有推远程。未纳入提交的是 `.cache/` 和 `.cursor/`。

### 7. Voicebox 做成并列插件，第一段只合成一次

结论：新增 `plugins/voicebox`，实现 Engine / Session。合成仍请求本机 Voicebox 的 `POST /generate/stream`。第一段用插件加载和一次模拟 HTTP 合成验收，不接 WebUI，也不要求本机 Voicebox 正在运行。服务地址默认 `http://localhost:17493`，可用环境变量 `ABOGEN_VOICEBOX_URL` 覆盖。

理由：用户选择形态 1。上游的合成入口是插件契约，Kokoro 和 Supertonic 都在 `plugins/` 里。WebUI 导入留到下一段，避免第一段同时改界面。这条决定放开了决策 6 里「先不开上游分支」的限制。

已执行：从本地引用 `upstream-check/main`（上游 `1807822`）建立分支 `port/voicebox-plugin`。没有把该引用配成 remote，没有合并安全分支。

### 8. 提交第一段，不推远程

结论：把 `plugins/voicebox`、`tests/test_voicebox_plugin.py` 和本决策记录提交到 `port/voicebox-plugin`。不推远程。

理由：用户选择提交。提交后这一段留在移植分支上，切换分支时不会把未提交的插件和决策记录一起带走。`.cache/` 与 `.cursor/` 仍然不纳入提交。

## 调查事实

- 本地 `origin` 是 `mikeduoxin/abogen`，它 fork 自 `denizsafak/abogen`。
- 安全分支保存的是 2025-12-16 提交之上的整份工作区。
- 那份工作区相对 fork 的 `origin/main`：184 个文件内容一致，12 个文件不同。独特功能主要是 Voicebox。
- 上游最新架构用 `abogen/tts_plugin` 加上 `plugins/kokoro`、`plugins/supertonic`。

### 9. 开始把 Voicebox 接到 WebUI

结论：音色导入和转换都走 `plugins/voicebox`。说话人里保存 Voicebox 的 profile id。导入从设置里的 Voicebox 地址拉取档案，名字写成 `VB: <名称>`。预览和正式转换用这个 id 调用插件。

理由：用户选择开始下一段。上游转换通过 `resolve_voice_target` 和 `create_pipeline_for_job` 选引擎，Voicebox 接在这两处，以及 Speaker Studio 的导入和预览。

已执行：

- 设置项 `voicebox_url`，默认 `http://localhost:17493`
- `POST /voices/voicebox/import` 与 Speaker Studio 的 Import Voicebox
- 转换和预览在 provider 为 voicebox 时创建 `voicebox` 插件

### 10. 提交 WebUI 接入，不推远程

结论：把这一段提交到 `port/voicebox-plugin`。不推远程。

理由：用户选择提交。导入、设置和转换属于同一段，留在移植分支上，切换分支时不会散掉。`.cache/` 与 `.cursor/` 仍然不纳入提交。

## 待定

- 安全分支和移植分支是否推到远程。当前不推。
