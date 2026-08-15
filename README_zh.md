<div align="center">

# unity-cli

**Unity Editor 的 AI 编程代理 skill — 适用于任何兼容 skills 的 Agent（Claude Code、Codex 等）**<br/>
**基于 [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Unity](https://img.shields.io/badge/Unity-2022-black.svg?logo=unity)](https://unity.com/)
[![Claude Code](https://img.shields.io/badge/Claude_Code-blueviolet.svg?logo=anthropic)](https://claude.ai/code)
[![Codex](https://img.shields.io/badge/Codex-black.svg?logo=openai&logoColor=white)](https://github.com/openai/codex)

57 个由 Unity 包提供的内置命令：默认六个创作域包含 51 个，另有 6 个显式控制面命令。<br/>
依赖 **[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)** —— 基于 Roslyn 的 Unity 交互式 C# REPL。

[快速开始](#-快速开始) · [使用方式](#-使用方式) · [命令](#-命令) · [自定义命令](#-自定义命令) · [架构](#️-架构)

[English](README.md) | 中文

</div>

---

```text
你：    “创建 10 个 Cube 围成一圈，每个加上 Rigidbody”
Agent： 完成。10 个 Cube 已在半径 5 处创建，均已添加 Rigidbody。
```

### ⚡ CLI + Skill

CLI 命令通过 Agent 的 Skill 体系提供。

- **节省 token。** Domain Index → Route Cards → Contract Bundle，避免把无关
  command schema 放进上下文。
- **与包一致。** 一次 fingerprint 比较即可把包拥有的内置和项目自定义
  contract 解析到每项目的机器本地缓存。
- **能力不封顶。** 没有结构化命令或可复用 snippet 能覆盖时，才回退到完整
  [Roslyn C# REPL](https://github.com/niqibiao/unity-csharpconsole)。
- **无需 sidecar。** 服务直接运行在 Unity Editor 内。
- **理解工作流。** 能处理 Unity 编译生命周期、Play Mode 和域重载。
- **运行时 / IL2CPP 支持。** 可配合 HybridCLR 用于运行时构建。
- **可演进 snippet 库。** 项目本地 C# snippet 带验证门、使用统计和老化机制。

### 🚀 快速开始

> [!IMPORTANT]
> **安装范围是 Unity 项目，不是全局。** 不要安装到用户主目录或全局 skills
> 目录。内置 CLI 会从自身提交位置向上查找所属 Unity 项目。

**1 · 安装 `unity-cli` skill：**

```bash
cd path/to/your/UnityProject
npx skills add niqibiao/unity-cli-skill --copy
```

**2 · 初始化：**

在 AI Agent 里运行 **`unity-cli setup`**。

**前置条件：** 兼容 skills 的 Agent（例如
[Claude Code](https://claude.ai/code) 或
[Codex CLI](https://github.com/openai/codex)）、用于 `npx` 的 Node.js、
Unity 2022，以及 Python 3.10+。

### 💬 使用方式

直接告诉 Agent 你想做什么：

```text
> 在场景里添加一个方向光，X 轴旋转 45 度
> 找出所有标签为 “Enemy” 的对象，列出它们的组件
> 截取 Scene View
> 开始 Profiler 录制，启用深度分析
```

Agent 会发现最小相关 command contract，验证 mutation，只有结构化路径不适用
时才编写 C#。

#### 🧩 一个 skill，多个子命令

所有功能都在一个 skill（`unity-cli`）中：

| 子命令 | 说明 |
|---|---|
| `cs setup` | 安装或检查 Unity 包版本 |
| `cs status` / `cs health` | 查看包与服务状态 |
| `cs list-commands` | 渐进发现包拥有的 contract |
| `cs command --input` | 预检并执行一个 canonical command |
| `cs batch --input` | 预检并在一次请求中执行 command workflow |
| `cs exec --file` | 以原始 C# 作为最终兜底 |
| `cs refresh` | 刷新资产并等待编译 |
| `cs catalog sync` / `cs catalog list` | 维护共享的自定义命令候选目录 |
| `cs snippets …` | 发现和维护可复用 C# snippet |

### 📦 命令

Unity 包是唯一的可执行 schema 权威。CLI 不再维护第二份内置参数/结果 manifest，
而是组合当前包的 Registry Snapshot 与一份很小的无 schema routing overlay。

渐进发现分三层：

```bash
# 1. 可选 Domain Index：相关 domain 已明确时跳过
cs list-commands --offline --json

# 2. 第一次 live discovery：限定范围的 Route Cards，仍不含参数/结果 schema
cs list-commands \
  --domain objects --domain assets --tier core --json

# 3. Contract Bundle：选中的 contract + 一层直接 relation
cs list-commands --offline \
  --id gameobject/create \
  --id gameobject/get \
  --json
```

Agent 会话中的第一次 live discovery 只比较一次 fingerprint。后续查询使用
`--offline` 和已验证的项目缓存。只有用户明确要求更新 command list 时才用
`--refresh` 强制获取完整 snapshot。

#### 默认创作域

| Domain | 范围 |
|---|---|
| `editor` | Editor 就绪状态、Play Mode 与 Console 诊断 |
| `scene` | 场景发现、加载、保存与层级 |
| `objects` | GameObject、组件、Transform 与选择 |
| `assets` | 项目资产与材质 |
| `prefabs` | Prefab 创建、实例化、检查与直接编辑 |
| `capture` | Scene/Game View 截图与 Profiler 录制 |

六个 registry/session 机制只在显式 control view 中出现：

```bash
cs list-commands --offline \
  --view control --domain control --tier control-plane --json
```

执行稳定的 canonical ID；包 contract 自己拥有内部 wire route：

```json
{"id":"gameobject/create","args":{"name":"Wall","primitiveType":"Cube"}}
```

通过 `cs command --json --input <file>` 传入 JSON。内置与自定义命令使用同一套
package-owned preflight。Unity 执行前，CLI 会拒绝过期 execution contract、未知
参数、错误类型/范围、歧义 selector 和不安全的空 mutation。

`editor/menu.open` 与 `editor/window.open` 只是 deny-policy intent，不是可执行
contract。精确 ID 发现会把它们作为不可执行的 `denied` 决策返回；skill 不会用
snippet 或原始 C# 绕过它们。

#### Snippet

| Action | 说明 |
|---|---|
| `list` / `show` / `search` | 发现可复用 snippet |
| `use` | 运行 snippet |
| `add` / `update` | 验证并维护 snippet |
| `deprecate` / `prune` | 淘汰 snippet |
| `stats` / `doctor` | 审计用量和库健康状态 |

### 🔧 自定义命令

自定义命令与内置命令使用相同的 package registry、canonical-ID discovery、
preflight 和 execution 路径：

```bash
cs list-commands --view custom --json
cs list-commands --offline --view custom --id teamtools/build_room --json
```

定义和注册方式请参考
[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)。

`cs catalog sync` 会从本次调用中已验证的 registry 生成确定性、可版本控制的候选
目录。`cs catalog list` 可以离线读取候选；当前包的 Registry Snapshot 始终是执行
权威。

### 🏗️ 架构

```text
AI Agent
  └─ unity-cli skill
      └─ 纯标准库 Python CLI
          ├─ 无 schema routing overlay
          ├─ fingerprint resolver + 机器本地 Registry Snapshot 缓存
          ├─ 渐进发现 + package-contract preflight
          └─ HTTP bridge
              └─ Unity Editor/Player 中的 com.zh1zh1.csharpconsole
                  ├─ package-owned registry（51 authoring + 6 control）
                  ├─ command handlers
                  └─ Roslyn compiler / REPL executor
```

CLI 从已安装的 Unity 包动态导入 client core，使 client 与 service 保持在同一
`major.minor` 版本线。项目根目录和服务端口会自动发现。

### ❓ 常见问题

| 问题 | 解决方案 |
|---|---|
| `service: UNREACHABLE` | 打开 Unity Editor 并加载项目 |
| `package: NOT FOUND` | 运行 `cs setup`，再让 Unity 解析包 |
| 端口冲突 | 服务会改用空闲端口；查看 `Temp/CSharpConsole/refresh_state.json` |
| 离线 custom unavailable | 运行一次 live `cs list-commands --view custom --json` |
| 版本不匹配 | 用 `cs status` 查看，再对齐包与 CLI 的 `major.minor` |

---

## License

[Apache-2.0](LICENSE)

---

如果这个 skill 对你有帮助，请给个 Star，让更多人发现它。
