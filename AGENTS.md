# AGENTS.md

Learnova 技能社区的分发工具（learnova-cli），以 agent skill 形式经 `npx skills` 分发，功能由纯 Python 脚本实现。

正式主仓与分发入口为 `https://git.nju.edu.cn/learnova/learnova-cli`，当前 `main` 跟踪 `njugit/main`。`origin` 是保留的 GitHub remote；同步 GitHub 不是主仓开发或发行的前置条件。

## 先读文档

改动任何代码前，按序阅读：

0. `internal/plan.md`（**如存在**）— 决策背景、分阶段实施计划、开放问题；本地文件，已排除出版本库，克隆环境不存在属正常
1. `docs/cli-spec.md` — 行为规范：命令面、配置、agent 适配、遥测、发布链路
2. `docs/architecture.md` — 实现架构：命令层、输出契约、数据源抽象、安装引擎、错误模型
3. `docs/mirror-spec.md` — 镜像仓规范：目录布局、index.json schema、同步约定
4. `docs/maintenance.md` — 维护手册：加新功能的步骤与规则

docs/ 回答「是什么」，`internal/plan.md` 回答「为什么」和「接下来做什么」。执行必须与设计文档一致；发现不一致先停下向维护者确认，不擅自偏离。

## 开发进度

- [x] 设计文档（cli-spec / mirror-spec / architecture）+ 按架构的 scripts 骨架
- [x] M1 镜像链路：`mirror/` producer 已实现并通过本地/生产快照验证；维护者确认同步服务已部署，正式镜像可匿名读取，不重复部署
- [x] M2 CLI 核心：MirrorSource、engine.install/uninstall、install/uninstall/upgrade 命令
- [x] M3 CLI：login/logout/whoami、publish/status、BackendSource、resolver 降级链、init 已实现并通过独立复核；遥测客户端已实现，需显式配置后端专用端点才发送
- [ ] M4 发布：正式主仓已上线且远端 main 与当前提交一致；待核实 `npx skills add`、实际 CI 多平台运行与 PowerShell 的验收记录

- [x] 本地文档构建：Markdown/PDF、宿主及自动判断、断点恢复和显式打包；官方云端 PDF 与自动模型构建已通过真实服务验收，本地 Gradio 依实际部署单独验收
- [x] 已复现缺陷：镜像缓存串源、跨 agent 升级回滚、构建返回过期请求等已修复，并覆盖回归与独立复核

进度须区分已有实现、待验收和外部状态待核实；通用部署检查表的未勾选项不等于未实现。对接后端前核对现有接口，不将旧计划中的接口缺口直接当作当前事实。

## 硬规则

- 运行时以 stdlib 为基线（Python 3.10+）；PDF 拆页与本地 Gradio 可按需惰性加载可选依赖，测试用 unittest 裸跑。
- handler 只返回 dict，不自行 print；错误抛 `CliError` 子类，不散落 try/except。
- 输出契约（JSON 字段名、error code、退出码）发布后只增不改不删。
- 用户级状态在 `~/.learnova/`；构建配置及任务可位于项目 `.learnova/`，不引入数据库。
- 遥测默认不收集，仅登录后上报 install 事件，fire-and-forget。
- 公开内容（代码、文档、commit）不得提及 Learnova 主仓的内部细节；后端地址默认值发行时注入，源码留空。镜像地址保留源码中的公开正式默认值，允许用户配置覆盖。
- 提交前跑 `python -m unittest discover tests -v`；镜像链路测试另跑 `python tests/t1_mirror_flow.py`。

## 目录速查

| 路径 | 职责 |
|---|---|
| `skills/learnova-cli/SKILL.md` | 实际分发给 agent 的 Skill 说明 |
| `skills/learnova-cli/scripts/learnova.py` | Router：参数解析、注册表分发、单点异常捕获 |
| `skills/learnova-cli/scripts/cli/commands/` | 每命令一文件 |
| `skills/learnova-cli/scripts/core/` | 纯逻辑零 IO（单测重点） |
| `skills/learnova-cli/scripts/sources/` | 数据源协议与实现、回退链 |
| `skills/learnova-cli/scripts/engine.py` | 安装引擎（文件操作唯一入口） |
| `skills/learnova-cli/scripts/store.py` | 本地 JSON 读写 |
| `mirror/` | 运维侧 one-shot 镜像 producer；读取 Learnova 权威数据并生成 Git mirror，不进入 npx skills 分发包 |
| `tests/test_mirror_sync.py` | producer 端到端测试：临时 SQLite + archive + 真实 Git repo |
| `internal/` | 本地实施记录，已排除出版本库，不存在属正常 |
