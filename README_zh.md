<div align="center">

# unity-cli

**Unity Editor 的 AI 编程代理 skill — Claude Code 与 Codex CLI**<br/>
**基于 [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Unity](https://img.shields.io/badge/Unity-2022.3%2B-black.svg?logo=unity)](https://unity.com/)
[![Claude Code](https://img.shields.io/badge/Claude_Code-blueviolet.svg?logo=anthropic)](https://claude.ai/code)

40+ 命令覆盖场景编辑、组件、资产、截图、性能分析等。<br/>
依赖 **[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)** — 基于 Roslyn 的 Unity 交互式 C# REPL。

[快速开始](#-快速开始) · [使用方式](#-使用方式) · [命令](#-命令) · [自定义命令](#-自定义命令) · [架构](#️-架构)

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
- **自我进化的代码片段库** — 项目本地 C# 片段（`.md` 文件，不参与编译），带验证门、使用频次跟踪、自动老化。通过 `cs snippets` 子命令发现和演化。


### 🚀 快速开始

> ⚠️ **执行 `npx skills add` 之前，务必先 `cd` 进入你的 Unity 项目根目录。**
> `npx skills add` 会装到**当前目录**的 Agent 文件夹里，所以 skill 必须落在**项目内**（随项目一起提交）。
> **不要在用户主目录 / 全局目录里执行：** CLI 通过从自身提交位置向上查找来定位 Unity 项目，装到主目录后
> 永远定位不到项目（只能退而依赖当前 shell 的 cwd，一旦换个目录运行就失效）；一份全局共享拷贝无法与每个
> 项目各自的 Unity 包保持版本一致；队友也无法通过 `git pull` 拿到它。

```bash
# 1. 先 cd 进入你的 Unity 项目根目录，再把 skill 装到这里（实体文件，可提交）。
#    npx 会自动识别你用的 Agent —— Claude Code（.claude/skills/）和 Codex（.agents/ + .codex/skills/）。
cd path/to/your/UnityProject
npx skills add niqibiao/unity-cli-skill --copy

# 2. 在 Agent 里运行 setup —— 它会把 Unity C# Console 包写进项目的 Packages/manifest.json，
#    然后打开 Unity 编辑器，让 Package Manager 解析这个包、启动 C# Console 服务。
> 安装 unity-cli      # 安装 com.zh1zh1.csharpconsole（已安装则做版本校验）

# 3. 验证
> 查看 unity-cli 状态
```

**前置条件：** [Claude Code](https://claude.ai/code) 或 [Codex CLI](https://github.com/openai/codex) 0.139+、Node.js（用于 `npx`）、Unity 2022.3+、Python 3.7+

### 💬 使用方式

直接告诉 Claude 你想做什么：

```
> 在场景里添加一个方向光，X 轴旋转 45 度
> 找出所有标签为 "Enemy" 的对象，列出它们的组件
> 截取 Scene View 的截图
> 开始 Profiler 录制，启用深度分析
```

Claude 会自动选择合适的命令，或在需要时编写 C# 代码。

#### 🧩 一个 skill，多个子命令

所有功能都在**一个 skill**（`unity-cli`）里；它的 `cs` 子命令覆盖全部操作，Agent 会自动触发
（Claude Code 和 Codex 通用）：

| 子命令 | 说明 |
| ----- | ---- |
| `cs setup` | 安装包到 manifest（已安装则做版本校验） |
| `cs status` / `cs health` | 包与服务状态 |
| `cs command --input` | 结构化 Unity 编辑器命令 |
| `cs exec` | 在编辑器中执行原始 C#（兜底） |
| `cs refresh` | 触发资产刷新 / 重编译 |
| `cs catalog sync` / `cs list-commands` | 自定义命令目录 + 维护者审计 |
| `cs snippets …` | 可复用 C# 片段库 |
| `cs snippets doctor` | 片段库健康审计 |


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

本 skill 为每个 Unity 项目维护一份持久化的自定义命令目录。运行 `cs catalog sync` 可从 Unity 拉取最新命令列表并缓存到磁盘；运行 `cs catalog list` 可在不连接编辑器的情况下离线查看已缓存的目录。

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

- **Skill 层**：Claude Code 和 Codex 调用的 `unity-cli` skill
- **CLI 层**：Python 调度器，将请求序列化为 JSON
- **Unity 层**：[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole) — HTTP 服务，自动发现命令处理器，Roslyn C# REPL

自动检测项目根目录和服务端口，无需手动配置。

### ❓ 常见问题


| 问题                     | 解决方案                                                       |
| ---------------------- | ---------------------------------------------------------- |
| `service: UNREACHABLE` | 确保 Unity 编辑器已打开并加载了项目                                      |
| `package: NOT FOUND`   | 运行 `cs setup` 添加包，再打开 Unity 解析它   |
| 端口冲突                   | 服务会自动切换到下一个可用端口，查看 `Temp/CSharpConsole/refresh_state.json` |
| 找不到命令                  | 确保包编译成功（Unity Console 中无报错）                                |
| 版本不匹配                  | 运行 `cs status` 查看版本；把 Unity 包对齐到 CLI 的 `major.minor`        |


---

## License

[Apache-2.0](LICENSE)

---

如果这个 skill 对你有帮助，请给个 Star，让更多人发现它。