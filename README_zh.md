<div align="center">

# unity-cli-plugin

**Unity Editor 的 AI 编程代理插件 — 支持 Claude Code 与 Codex CLI**<br/>
**基于 [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Unity](https://img.shields.io/badge/Unity-2022.3%2B-black.svg?logo=unity)](https://unity.com/)
[![Claude Code](https://img.shields.io/badge/Claude_Code-blueviolet.svg?logo=anthropic)](https://claude.ai/code)

40+ 命令覆盖场景编辑、组件、资产、截图、性能分析等。<br/>
依赖 **[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)** — 基于 Roslyn 的 Unity 交互式 C# REPL。

[快速开始](#-快速开始--claude-code) · [使用方式](#-使用方式) · [命令](#-命令) · [自定义命令](#-自定义命令) · [架构](#️-架构)

[English](README.md) | 中文

</div>

---

```
你：     "创建 10 个 Cube 围成一圈，每个加上 Rigidbody"
Claude:  完成。10 个 Cube 已在半径 5 处创建，均已添加 Rigidbody 组件。
```

### ⚡ CLI + Skill

通过 Claude Code 的 Skill 体系暴露 CLI 命令。

- **省 token。** Skill 按需加载。
- **无限制。** 可回退到完整的 [Roslyn C# REPL](https://github.com/niqibiao/unity-csharpconsole) —— 不受预定义工具限制。
- **无需额外进程。** 服务运行在 Unity Editor 进程内，零额外基础设施。
- **感知工作流。** 理解 Unity 的编译生命周期、Play Mode、域重载。
- **自定义命令自动发现。** 用户定义的 C# 命令会自动同步到 Skill 目录。
- **运行时 / IL2CPP 支持。** 配合 HybridCLR 可在运行时构建中使用。
- **自我进化的代码片段库** — 项目本地 C# 片段（`.md` 文件，不参与编译），带验证门、使用频次跟踪、自动老化。通过 `cs snippets` 子命令和 `unity-cli-snippets` skill 发现和演化。


### 🚀 快速开始 — Claude Code

**前置条件：** [Claude Code](https://claude.ai/code)、Unity 2022.3+、Python 3.7+

```bash
# 1. 添加市场源并安装插件
claude plugin marketplace add niqibiao/unity-cli-plugin
claude plugin install unity-cli-plugin

# 2. 安装 Unity 包（在项目目录下）—— 直接让 Claude 做：
claude
> 安装 unity-cli

# 3. 验证
> 查看 unity-cli 状态
```

### 🤖 快速开始 — Codex CLI

同一套插件是双 Agent 的：所有功能都以 Skill 形式提供，两个 Agent 共享（不再有斜杠命令）。在
Codex 中安装插件后，通过 `unity-cli-setup` skill 完成安装。

**前置条件：** [Codex CLI](https://github.com/openai/codex) 0.139+、Unity 2022.3+、Python 3.7+

在 Unity 项目目录下的 Codex 会话中，让它安装 unity-cli。`unity-cli-setup` skill 会先把
CLI 引导到一个稳定路径，再安装 Unity 包并验证连接：

```bash
# unity-cli-setup skill 会完成引导与安装；之后两个 Agent 都用这一条稳定路径，
# 例如验证连接：
python "$HOME/.unity-cli-plugin/current/cli/cs.py" status --project "$(pwd)"
```

CLI 会被复制到 `$HOME/.unity-cli-plugin/current/cli/`，两个 Agent 都用这一个稳定路径调用。
插件升级后，稳定副本会在下一条命令时自动检测并重新复制，无需手动刷新。

### 🔒 团队版本管理

把整个团队锁定到同一版本，并通过改提交进仓库的文件来统一升级所有人。一共**三个版本旋钮**——
保持它们在同一 `major.minor`（patch 号可因仓库而异），避免 `⚠ version mismatch`。

**1. Claude Code 插件** —— 提交到 `.claude/settings.json`：

```json
{
  "extraKnownMarketplaces": {
    "unity-cli-plugin": {
      "source": { "source": "github", "repo": "niqibiao/unity-cli-plugin", "ref": "v1.5.1" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "unity-cli-plugin@unity-cli-plugin": true }
}
```

`source.ref`（tag/commit）锁死版本；`autoUpdate: true` 在每次会话启动把成员对齐到提交的 `ref`（自动修复漂移，无需手动 `/plugin`）。版本写在 `source.ref`，**不是** `enabledPlugins` 的 key——key 是 `plugin-id@marketplace-id`，不支持版本语法。

**2. Codex CLI 插件** —— 提交到 `.agents/plugins/marketplace.json`：

```json
{
  "name": "unity-cli-pinned",
  "plugins": [
    {
      "name": "unity-cli-plugin",
      "source": { "source": "url", "url": "https://github.com/niqibiao/unity-cli-plugin.git", "ref": "v1.5.1" }
    }
  ]
}
```

`url` 源的 `ref`（tag）或 `sha`（commit）锁死版本。Codex **没有 `autoUpdate` 等价物**：clone 后每人首次要装一次 + reload（`/plugin install`，再 `/reload-plugins`，或重启 Codex）。之后 bump `ref` 需要 `codex plugin marketplace upgrade` + reload。

**3. Unity 包** —— pin 在 `Packages/manifest.json`（由 `cs setup` 管理）：

```json
{ "dependencies": { "com.zh1zh1.csharpconsole": "https://github.com/niqibiao/unity-csharpconsole.git#v1.5.0" } }
```

**统一升级团队：** 把各处 pin 改成对应仓库的新 tag（插件与包保持同一 major.minor），提交并推送。成员下次开会话时：Claude 自动更新；Codex 跑一次 `marketplace upgrade` + reload；Unity 在编辑器打开时重新 resolve 包。

> Claude（`autoUpdate`）完全无感。Codex 只锁源、**不会** clone 即自动安装——首次安装和每次升级都要手动 reload/重启。

### 💬 使用方式

直接告诉 Claude 你想做什么：

```
> 在场景里添加一个方向光，X 轴旋转 45 度
> 找出所有标签为 "Enemy" 的对象，列出它们的组件
> 截取 Scene View 的截图
> 开始 Profiler 录制，启用深度分析
```

Claude 会自动选择合适的命令，或在需要时编写 C# 代码。

#### 🧩 Skills

所有功能都是 Skill —— Claude 会根据你的请求自动触发（Claude Code 和 Codex 通用）：

| Skill                         | 说明              |
| ----------------------------- | --------------- |
| `unity-cli-setup`            | 安装 Unity 包（跨 Agent 引导） |
| `unity-cli-status`           | 检查包和服务状态        |
| `unity-cli-refresh`          | 触发资产刷新 / 重编译    |
| `unity-cli-refresh-commands` | 刷新每项目自定义命令缓存    |
| `unity-cli-sync-catalog`     | 审计内置命令表与实时 Editor 是否一致（维护者用） |
| `unity-cli-command`          | 结构化 Unity 编辑器命令 |
| `unity-cli-exec-code`        | 在编辑器中执行原始 C#（兜底） |
| `unity-cli-snippets`         | 可复用 C# 片段库      |
| `unity-cli-snippets-audit`   | 片段库健康审计         |


#### 💻 直接使用 CLI

```bash
python cli/cs.py exec --json --project . "Debug.Log(\"Hello\")"
python cli/cs.py command --json --project . gameobject create '{"name":"Cube","primitiveType":"Cube"}'
python cli/cs.py refresh --json --project . --exit-playmode --wait 60
python cli/cs.py batch --json --project . '[{"ns":"gameobject","action":"create","args":{"name":"A"}},{"ns":"gameobject","action":"create","args":{"name":"B"}}]'
python cli/cs.py list-commands --json --project . --timeout 10
python cli/cs.py catalog sync --json --project .
python cli/cs.py catalog list --json --project .
python cli/cs.py snippets list --json --project .
python cli/cs.py snippets search "physics" --json --project .
```

### 📦 命令

13 个命名空间、50 个内置命令。所有命令支持 `--json` 输出。

#### gameobject


| Action       | 说明                       |
| ------------ | ------------------------ |
| `find`       | 按名称、标签或组件类型查找 GameObject |
| `create`     | 创建新 GameObject（空对象或基本体）  |
| `destroy`    | 销毁 GameObject            |
| `get`        | 获取 GameObject 详细信息       |
| `modify`     | 修改名称、标签、层、激活状态或静态标记      |
| `set_parent` | 设置父对象                    |
| `duplicate`  | 复制 GameObject            |


#### component


| Action   | 说明                |
| -------- | ----------------- |
| `add`    | 为 GameObject 添加组件 |
| `remove` | 移除组件              |
| `get`    | 获取组件的序列化字段数据      |
| `modify` | 修改组件的序列化字段        |


#### transform


| Action | 说明                    |
| ------ | --------------------- |
| `get`  | 获取位置、旋转和缩放            |
| `set`  | 设置位置、旋转和/或缩放（本地或世界坐标） |


#### scene


| Action      | 说明                 |
| ----------- | ------------------ |
| `hierarchy` | 获取完整场景层级树，可选包含组件信息 |


#### prefab


| Action        | 说明                            |
| ------------- | ----------------------------- |
| `create`      | 从场景中的 GameObject 创建 Prefab 资产 |
| `instantiate` | 将 Prefab 实例化到当前场景             |
| `unpack`      | 解包 Prefab 实例                  |


#### material


| Action   | 说明                 |
| -------- | ------------------ |
| `create` | 创建新材质（指定 Shader）   |
| `get`    | 获取材质属性             |
| `assign` | 将材质分配给 Renderer 组件 |


#### screenshot


| Action       | 说明                  |
| ------------ | ------------------- |
| `scene_view` | 截取 Scene View 到图片文件 |
| `game_view`  | 截取 Game View 到图片文件  |


#### profiler


| Action   | 说明                     |
| -------- | ---------------------- |
| `start`  | 开始 Profiler 录制（可选深度分析） |
| `stop`   | 停止 Profiler 录制         |
| `status` | 获取当前 Profiler 状态       |
| `save`   | 保存录制数据到 `.raw` 文件      |


#### editor


| Action            | 说明                    |
| ----------------- | --------------------- |
| `status`          | 获取编辑器状态和 Play Mode 信息 |
| `playmode.status` | 获取当前 Play Mode 状态     |
| `playmode.enter`  | 进入 Play Mode          |
| `playmode.exit`   | 退出 Play Mode          |
| `menu.open`       | 按路径执行菜单项              |
| `window.open`     | 按类型名打开编辑器窗口           |
| `console.clear`   | 清空编辑器控制台              |
| `console.mark`    | 向编辑器日志写入可搜索标记         |


#### asset


| Action          | 说明                   |
| --------------- | -------------------- |
| `move`          | 移动或重命名资产             |
| `copy`          | 将资产复制到新路径            |
| `delete`        | 删除一个或多个资产            |
| `create_folder` | 在 Asset Database 创建文件夹 |


#### project


| Action           | 说明          |
| ---------------- | ----------- |
| `scene.list`     | 列出项目中所有场景   |
| `scene.open`     | 按路径打开场景     |
| `scene.save`     | 保存当前场景      |
| `selection.get`  | 获取当前编辑器选中对象 |
| `selection.set`  | 设置编辑器选中对象   |
| `asset.list`     | 按类型筛选列出资产   |
| `asset.import`   | 按路径导入资产     |
| `asset.reimport` | 按路径重新导入资产   |


#### session


| Action    | 说明            |
| --------- | ------------- |
| `list`    | 列出活跃的 REPL 会话 |
| `inspect` | 查看会话状态        |
| `reset`   | 重置会话的编译器和执行器  |


#### command


| Action | 说明                  |
| ------ | ------------------- |
| `list` | 列出所有已注册命令（内置 + 自定义） |


#### snippets


| Action      | 说明                         |
| ----------- | -------------------------- |
| `list`      | 浏览本地代码片段库                  |
| `show`      | 查看片段的完整内容和元数据              |
| `search`    | 按关键词搜索片段                   |
| `use`       | 运行片段（执行其 C# 代码）            |
| `add`       | 向片段库添加新片段                  |
| `update`    | 更新已有片段                     |
| `deprecate` | 将片段标记为已废弃                  |
| `prune`     | 移除已老化或废弃的片段                |
| `stats`     | 查看片段库的使用统计                 |


### 🔧 自定义命令

支持自定义命令。定义和注册方式请参考 [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)。

插件为每个 Unity 项目维护一份持久化的自定义命令目录。运行 `cs catalog sync` 可从 Unity 拉取最新命令列表并缓存到磁盘；运行 `cs catalog list` 可在不连接编辑器的情况下离线查看已缓存的目录。

### 🏗️ 架构

```
Claude Code                      Unity Editor
┌──────────────────┐            ┌──────────────────────────┐
│  Skills          │            │  com.zh1zh1.csharpconsole│
│  ┌────────────┐  │            │  ┌────────────────────┐  │
│  │ cli-command│──┼── HTTP ──▶ │  │ ConsoleHttpService │  │
│  │ cli-exec   │  │            │  │  ├─ CommandRouter  │  │
│  └────────────┘  │            │  │  ├─ REPL 编译器     │  │
│                  │            │  │  └─ REPL 执行器     │  │
│  Python CLI      │            │  └────────────────────┘  │
│  ┌────────────┐  │            │                          │
│  │ cs.py      │  │            │  40+ CommandActions      │
│  │ core_bridge│  │            │  (GameObject, Component, │
│  └────────────┘  │            │   Prefab, Material, ...) │
└──────────────────┘            └──────────────────────────┘
```

- **插件层**：Claude Code 和 Codex 调用的 Skills
- **CLI 层**：Python 调度器，将请求序列化为 JSON
- **Unity 层**：[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole) — HTTP 服务，自动发现命令处理器，Roslyn C# REPL

自动检测项目根目录和服务端口，无需手动配置。

### ❓ 常见问题


| 问题                     | 解决方案                                                       |
| ---------------------- | ---------------------------------------------------------- |
| `service: UNREACHABLE` | 确保 Unity 编辑器已打开并加载了项目                                      |
| `package: NOT FOUND`   | 运行 `unity-cli-setup` skill，或检查 `Packages/manifest.json`     |
| 端口冲突                   | 服务会自动切换到下一个可用端口，查看 `Temp/CSharpConsole/refresh_state.json` |
| 找不到命令                  | 确保包编译成功（Unity Console 中无报错）                                |
| 版本不匹配                  | 运行 `unity-cli-status` skill 查看版本信息，如协议版本不同请更新包             |


---

## License

[Apache-2.0](LICENSE)

---

如果这个插件对你有帮助，请给个 Star，让更多人发现它。