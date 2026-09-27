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
- `install_scope` 只由 install handler 解释；其他带 scope 的命令只响应显式 `--project`。

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
- Resolver 是唯一的源决策者：最终形态默认 Backend 优先、失败降级 Mirror；当前后端未配置时直接选 Mirror，完整的在线回退链在后端数据源接入时实现。
- `--offline` 只用于严格离线检索；MirrorSource 在该模式下不得发起网络请求。
- 命令层只面向协议编程，不碰 urllib、不知道背后是哪个源。
- 扩展：新源 = 实现协议 + 注册进 Resolver；改回退策略只动 Resolver；测试注入 FakeSource。

## 4. 安装引擎：命令共享的领域服务

install / list / uninstall / upgrade 共享 `engine.py`，命令只做薄壳（解析参数 → 调 source / engine → 返回 dict），命令层零文件操作。安装过程中使用的临时工作目录由 engine 管理。

- **安装事务**：先验证技能包，再将所有物理目标复制到目标旁临时目录；提交阶段失败时回滚已替换目标，避免多个 agent 处于不同版本。
- **状态重建**：不维护安装记录，真相在 agent 目录里；`list`/`upgrade` 扫目录读 frontmatter 重建。
- **与 Registry 配合**：`agents.py` 将逻辑 agent 映射为物理 InstallTarget。同一物理目录可关联多个 agent，只执行一次文件操作，但结果保留全部逻辑 agent 归属；加 agent 只改 Registry 数据表。

## 5. 错误模型与横切关注点

- 扁平异常树：`CliError(code, message, exit_code)`，仅按退出码分子类（`AuthError`=4、`ConflictError`=3）。
- `code` 是稳定机器可读标识，属于契约；`message` 给人看，可自由措辞。
- 错误只向上抛，Router 单点捕获；命令与引擎不写 try/except。

横切关注点各自收敛一处：

| 关注点 | 模块 | 约定 |
|---|---|---|
| HTTP | `net.py` | 统一超时（默认 10s）、UA、错误转换为 CliError |
| 遥测 | `telemetry.py` | fire-and-forget，独立 try，失败静默 |
| 本地文件 | `store.py` | 原子写（临时文件 + rename）、凭证 600 |
| 纯逻辑 | `core/` | 零 IO 纯函数：frontmatter、版本比较、index 解析、errors |

网络层带认证请求不得把 Authorization 跟随到不同 origin 的重定向目标；镜像 JSON、文本与 archive 下载均设置体积上限。

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
│   ├── skill.py
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
├── net.py               # urllib 封装（避免与标准库 http 同名遮蔽）
└── telemetry.py
```

## 测试策略

分层设计见 [ci.md](ci.md)（T0 单元 / T1 镜像链路 / T2 后端）。运行层面：

- `core/` 纯函数直接单测（版本比较、frontmatter、index 过滤排序、升级计划是重点）。
- T1 端到端：本地 GitLab stub + 临时 `LEARNOVA_HOME`/`HOME`，子进程跑命令，不碰网络与真实 home。
- `net.py` 等薄封装手工验证，不强行单测真实网络。
- 测试用 unittest 裸跑，运行时与测试均保持零依赖纯 stdlib。
