# 实现架构

Learnova CLI 的内部架构约定。行为规范见 [cli-spec.md](cli-spec.md)，本文约束代码怎么组织。

设计参照 vercel-labs/skills 的分层（Router / Source Resolver / Agent Registry / Parser）与 Agent Skills 规范对脚本的要求（自包含、结构化输出、渐进式披露）。

## 数据流总览

```
终端参数 → Router(注册表分发) → command handler
  → Resolver(选数据源) → SkillSource(拿数据)
  → engine(操作文件) / store(本地状态)
  → handler 返回 dict → Presenter(渲染输出)
任何一步出错 → 抛 CliError → Router 单点捕获 → 统一错误输出
```

## 1. 命令层：注册表式 Command 模式

- 每个命令一个模块，暴露 `register(subparsers)` 与 `handler(args, ctx) -> dict`。
- Router（`learnova.py`）只做三件事：构建 argparse、按注册表分发、单点异常捕获。
- 新命令 = 新模块 + 注册表一行，Router 与其他命令不改。
- `ctx`（CliContext：配置、凭证、路径、`--json`/`--offline` 标志）显式注入 handler，是测试接缝——测试造假 ctx，handler 不改代码即可单测。

## 2. 输出契约：结构化优先

- handler 只返回 dict，**禁止自行 print**。
- Presenter 统一渲染：`--json` 原样输出；否则人类可读格式。人类格式是渲染结果，可随意调整；dict 字段名属于契约，发布后不随便改。
- 错误同样走 Presenter：`{"error": {"code": "...", "message": "..."}}`。
- 退出码表：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 通用错误 |
| 2 | 参数/用法错误 |
| 3 | 冲突（如同名技能已存在） |
| 4 | 未认证/凭证失效 |

## 3. 数据源抽象：Strategy + 回退链

- `SkillSource` 协议（端口）：`search` / `info` / `fetch` 三个能力。
- 两个适配器：`BackendSource`（后端 API）、`MirrorSource`（index.json 缓存 + GitLab archive 按 commit 下载）。
- Resolver 是唯一的源决策者：默认 Backend 优先、失败降级 Mirror；`--offline` 强制 Mirror。
- 命令层只面向协议编程，不碰 urllib、不知道背后是哪个源。
- 扩展：新源 = 实现协议 + 注册进 Resolver；改回退策略只动 Resolver；测试注入 FakeSource。

## 4. 安装引擎：命令共享的领域服务

install / list / uninstall / upgrade 共享 `engine.py`，命令只做薄壳（解析参数 → 调 source → 调 engine → 返回 dict），命令层零文件操作。

- **原子拷贝**：先复制到目标旁临时目录，全部成功后 `rename`；中断最坏留下无害临时目录，不留半截坏技能。
- **状态重建**：不维护安装记录，真相在 agent 目录里；`list`/`upgrade` 扫目录读 frontmatter 重建。
- **与 Registry 配合**：`agents.py` 只回答「涉及哪些目录」（检测 × 用户级/项目级），引擎对目录组统一操作；加 agent 只改 Registry 数据表。

## 5. 错误模型与横切关注点

- 扁平异常树：`CliError(code, message, exit_code)`，仅按退出码分子类（`AuthError`=4、`ConflictError`=3）。
- `code` 是稳定机器可读标识，属于契约；`message` 给人看，可自由措辞。
- 错误只向上抛，Router 单点捕获；命令与引擎不写 try/except。

横切关注点各自收敛一处：

| 关注点 | 模块 | 约定 |
|---|---|---|
| HTTP | `http.py` | 统一超时（默认 10s）、UA、错误转换为 CliError |
| 遥测 | `telemetry.py` | fire-and-forget，独立 try，失败静默 |
| 本地文件 | `store.py` | 原子写（临时文件 + rename）、凭证 600 |
| 纯逻辑 | `core/` | 零 IO 纯函数：frontmatter、版本比较、index 解析、errors |

## 模块布局

```
scripts/
├── learnova.py          # Router：argparse + 注册表 + 单点异常捕获
├── cli/
│   ├── context.py       # CliContext（注入接缝）
│   ├── presenter.py     # dict → json/人类可读；错误格式化
│   └── commands/        # 每命令一文件：register() + handler()
├── core/                # 纯逻辑零 IO
│   ├── errors.py
│   ├── frontmatter.py
│   ├── versions.py
│   └── index.py
├── sources/
│   ├── base.py          # SkillSource 协议
│   ├── backend.py
│   ├── mirror.py
│   └── resolver.py      # 回退链
├── agents.py            # Registry：映射数据表 + 检测
├── engine.py            # 安装引擎
├── store.py             # ~/.learnova 读写
├── http.py              # urllib 封装
└── telemetry.py
```

## 测试策略

- `core/` 纯函数直接单测（版本比较、frontmatter、index 过滤是重点）。
- 命令层用假 ctx + FakeSource 注入测，不碰网络与真实 home 目录。
- `http.py` 等薄封装手工验证，不强行单测真实网络。
- pytest 仅 dev 依赖，运行时保持零依赖纯 stdlib。
