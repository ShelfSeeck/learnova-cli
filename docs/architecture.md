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
- Resolver 是唯一的源决策者：有后端配置及凭证时 Backend 优先，可恢复网络/服务/认证失败时降级 Mirror；严格离线、后端未配置或无凭证时直接选 Mirror。非法响应、包错误和明确的不存在错误不降级。
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

`auth.py` 收敛登录交互、origin 绑定的凭证刷新与认证请求；命令不读写 Cookie、不组装 nonce。net 的新参数和响应头能力保持原请求签名兼容，空 204 响应可返回 None。发布目录扫描和 ZIP 操作收敛到发布服务，复用安全纯校验；业务错误仍统一向 Router 抛出。

遥测使用独立短命进程，访问凭证仅经私有管道传递，工作进程不得刷新、保存或清除账号状态。它只读当前同源镜像缓存，以唯一目录及版本定位 community_id 后发送原四字段事件，缺映射或认证失败静默放弃。发布与构建打包共享纯隐藏路径排除规则。

## 6. 镜像生产端：独立 one-shot 工具

用户侧 CLI 负责消费 Git 镜像；镜像生产端负责把 Learnova 的 APPROVED 社区数据投影成该 Git 镜像。两者共享 `mirror-spec.md` 契约，但运行位置和生命周期不同。

镜像生产端归属 learnova-cli 仓库，不进入 `skills/learnova-cli/` 的 npx skills 分发包。Learnova 当前只作为只读数据源，不 import 镜像代码，也不需要为镜像增加数据库字段。

```text
Learnova SQLite + archived_skill/
              │
              │ read-only
              ▼
mirror/sync.py
              │
              │ commit + push
              ▼
Git mirror
              │
              │ read
              ▼
skills/learnova-cli/scripts/sources/mirror.py
```

`mirror/sync.py` 是幂等 one-shot 入口，不实现常驻循环。调度由 systemd timer、cron、Task Scheduler、CronJob 或 CI 等外部机制提供。核心代码不得依赖任一调度器或 Linux 专有服务管理接口。

同步过程中的持久状态以 Git 镜像本身为准：当前映射来自 `index.json`，历史版本与曾经使用过的目录可从 Git commit/history 恢复。不要再引入与 Git 镜像并行、需要单独备份的私有同步状态库。

## 模块布局

```
skills/learnova-cli/
├── SKILL.md             # npx skills 分发入口
├── learnova-cli         # POSIX 薄入口
├── learnova-cli.ps1     # PowerShell 薄入口
└── scripts/
    ├── learnova.py      # Router：argparse + 注册表 + 单点异常捕获
    ├── cli/
    │   ├── context.py   # CliContext（注入接缝）
    │   ├── presenter.py # dict → json/人类可读；错误格式化
    │   └── commands/    # 每命令一文件：register() + handler()
    ├── core/            # 纯逻辑零 IO
    │   ├── errors.py
    │   ├── frontmatter.py
    │   ├── skill.py
    │   ├── versions.py
    │   └── index.py
    ├── sources/
    │   ├── base.py      # SkillSource 协议
    │   ├── backend.py
    │   ├── mirror.py
    │   └── resolver.py  # 回退链
    ├── agents.py        # Registry：映射数据表 + 检测
    ├── engine.py        # 安装引擎
    ├── store.py         # ~/.learnova 读写
    ├── net.py           # urllib 封装（避免与标准库 http 同名遮蔽）
    ├── auth.py          # 账号、凭证刷新和同源认证请求
    ├── bootstrap.py     # 初始化预览与终端确认
    ├── publishing.py    # 安全打包、提交与审核查询
    └── telemetry.py

mirror/
├── sync.py              # 跨平台 one-shot 同步入口
├── source.py            # Learnova SQLite / archive 只读适配
├── repository.py        # Git 工作树、commit、push 与恢复
├── index.py             # index.json 生成和校验
└── deploy/              # 可选调度器模板；核心逻辑不依赖它
```

## 测试策略

分层设计见 [ci.md](ci.md)（T0 单元 / T1 镜像链路 / T2 后端）。运行层面：

- `core/` 纯函数直接单测（版本比较、frontmatter、index 过滤排序、升级计划是重点）。
- T1 端到端：本地 GitLab stub + 临时 `LEARNOVA_HOME`/`HOME`，子进程跑命令，不碰网络与真实 home。
- `net.py` 等薄封装手工验证，不强行单测真实网络。
- 测试用 unittest 裸跑，运行时与测试均保持零依赖纯 stdlib。

文档构建以 `core/document.py`、`core/chunking.py`、`core/skill_build.py` 处理纯文本与数据，不执行 IO。项目任务 IO 归构建 jobs，配置与原子 JSON 归 store，正式目录事务归 engine；解析不属于 SkillSource。可选 PDF 拆页和本地 Gradio 依赖仅在使用时加载。

正式目录事务由 `engine.commit_directories(candidate, outputs, force)` 共享：调用方先验证候选，输出是明确的最终目录列表，不要求社区版本。所有目标先暂存再替换，失败清理并回滚；拒绝不安全和符号链接目标。社区安装仍单独校验版本。本地构建来源记录仅为 schema_version 2、source local-build、version null；来源扫描兼容旧 schema 1 的基础标记，不递归读取或校验文件；社区 sidecar 有效且目录匹配时优先。项目管理额外扫描统一目录并按物理路径去重；升级仅选择社区来源的具体路径。

目录提交失败后逐个尝试恢复全部目标。若部分恢复失败，保留受阻目标的备份并清理其余暂存目录；返回 `DIRECTORY_ROLLBACK_FAILED`（退出码 1），消息包含提交错误、各恢复错误及备份位置，异常链保留原提交错误。

PDF 适配器位于 `build/parsers.py`，通过 `advance(source, config, state, checkpoint)` 返回 running/progress 或 completed/markdown/assets/page_map。私有状态按分片保存安全任务标识及已完成内容（图片为 base64）；每次跨请求边界先调用原子 checkpoint。远端走共享 net，本地 SDK 仅存在于有时限的隔离子进程中，日志不进入命令输出。配置的 `PARSING_KEYS/CHUNKING_KEYS` 是任务摘要和失效的唯一依赖清单。

`net.request` 保持既有 JSON 签名，新增 `request_bytes` 支持原始 PUT 与有限二进制响应。请求和读取具有超时与长度上限，解析 ZIP 另查成员和总展开长度。错误不回显服务器正文、原始异常或 URL 查询/用户信息；认证请求继续禁止跨 origin 重定向，签名上传/下载从不附加 bearer。

`core/page_provenance.py` 仅做数值映射验证和编辑偏移转换，无 IO。解析器先把每个分片的已验证页映射转换到合并 Markdown；pipeline 再按图片归一化记录转换，白名单数值范围仅保存在私有任务。旧注入解析器的 page_map 仍可私有保存，格式未知时只标 unavailable，不拷贝到公开来源记录。

### 自动构建判断适配器

自动模式通过 `build.decisions.Decision` 调用配置的 OpenAI 兼容 `/chat/completions` 接口，使用环境变量名引用密钥。模型只能返回判断结果或分页读取请求；读取经构建服务执行，受请求次数、上下文大小和输出大小限制。结果仍由统一的 `submit` 校验；非法响应、非法方案和网络失败保留待处理请求，返回固定错误信息。提示词规则版本纳入语义阶段有效性校验。`run` 在解析服务返回进行中时等待后再轮询，`next` 只即时轮询一次；`--until` 在进入指定阶段时停止。

生成的知识 Skill 说明如何按主题选择参考文件，并将参考内容视为来源数据。生成索引不指示查询来源摘要或页码侧车；参考文件原文不因说明文字变化而改写。

完成状态是历史记录。status/next/普通 resume 不检查原始输入、成品或新规则。显式 resume --from 是重建边界，重新判断私有依赖并要求明确的冲突覆盖授权；有效解析和判断可复用。活动任务仍用 seals 验证私有状态，渲染版本变化只让活动索引失效，判断规则变化只让活动切片失效。

`core/skill_build.py` 分开两种纯校验：生成验证显式私有 chunks 的精确切片、连续覆盖和保护范围；当前目录验证名称/描述、路径和有效链接，不依赖历史摘要或来源记录。超大原因仅在生成时校验白名单，作为静态导航提示和私有任务信息保留。package 在 jobs 的锁内读取当前目录，排除管理记录、私有状态、VCS 和凭证，拒绝链接/特殊文件；不读取原输入，不修改完成状态。待提交摘要仅用于中断事务恢复，成功后清除。
