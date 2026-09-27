# AGENTS.md

Learnova 技能社区的分发工具（learnova-cli），以 agent skill 形式经 `npx skills` 分发，功能由纯 Python 脚本实现。

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
- [ ] M1 镜像链路：镜像仓初始化 + 同步脚本 + index.json 生成（主项目侧）
- [x] M2 CLI 核心：MirrorSource、engine.install/uninstall、install/uninstall/upgrade 命令
- [ ] M3 账号与发布：login/publish/status、遥测、BackendSource、resolver 降级链、init
- [ ] M4 发布：GitHub 建仓、`npx skills add` 验证、PowerShell 实测

## 硬规则

- 运行时零第三方依赖（纯 stdlib，Python 3.10+）；测试用 unittest 裸跑，同样无第三方包。
- handler 只返回 dict，不自行 print；错误抛 `CliError` 子类，不散落 try/except。
- 输出契约（JSON 字段名、error code、退出码）发布后只增不改不删。
- 本地状态只在 `~/.learnova/`（JSON 文件），不引入数据库。
- 遥测默认不收集，仅登录后上报 install 事件，fire-and-forget。
- 公开内容（代码、文档、commit）不得提及 Learnova 主仓的内部细节；后端/镜像地址默认值发行时注入，源码留空。
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
| `internal/` | 本地实施记录，已排除出版本库，不存在属正常 |
