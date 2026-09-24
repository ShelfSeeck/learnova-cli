# 维护手册

加新功能或修改行为时按本手册执行。总原则：**先改设计文档，再改代码**；代码必须与 `cli-spec.md` / `architecture.md` / `mirror-spec.md` 保持一致。

## 加一个新命令

1. 在 `docs/cli-spec.md` §2 命令面中登记命令、参数与默认行为。
2. 新建 `scripts/cli/commands/<name>.py`，实现 `register(sub)` 与 `handler(args, ctx) -> dict`。
3. 在 `scripts/cli/commands/__init__.py` 的 `MODULES` 注册一行。
4. handler 内：解析参数 → 经 `sources/resolver.py` 取数据（如需要）→ 经 `engine.py` 操作文件（如需要）→ 返回 dict。
5. 需要新 error code 时，在本文「错误码登记表」登记。
6. 加测试：命令逻辑用假 ctx + FakeSource 注入测；纯逻辑抽到 `core/` 单测。

禁止：handler 里 print、直接调 urllib、直接读写 `~/.learnova/`（走 `store.py`）、直接操作文件系统（走 `engine.py`）。

## 加一个新 agent 适配

1. 在 `scripts/agents.py` 的 `AGENTS` 表中加一行（`detect_dir` / `global_skills` / `project_skills`）。
2. 同步更新 `docs/cli-spec.md` §3 的映射表。
3. 目标路径必须在真实环境实测确认后在 PR 描述中说明依据。

## 加一个新数据源

1. 在 `scripts/sources/` 新建模块，实现 `SkillSource` 协议（`search` / `info` / `fetch`）。
2. 在 `sources/resolver.py` 注册并明确其在回退链中的位置。
3. 更新 `docs/architecture.md` §3 与 `docs/cli-spec.md` §5 检索策略。

## 修改输出契约

- JSON 字段、error code、退出码发布后**只增不改不删**。必须变更时，提升 schema 版本并在 `docs/cli-spec.md` 中注明迁移说明。
- 人类可读输出不属于契约，可自由调整。

## 错误码登记表

新 error code 在此登记（code、含义、退出码）：

| code | 含义 | 退出码 |
|---|---|---|
| `NOT_IMPLEMENTED` | 功能尚未实现 | 1 |
| `BAD_USAGE` / `BAD_CONFIG_KEY` | 参数或配置项错误 | 2 |
| `SKILL_CONFLICT` | 目标目录已存在同名技能 | 3 |
| `UNAUTHORIZED` | 凭证失效或未登录 | 4 |
| `HTTP_ERROR` / `UNREACHABLE` | 后端 HTTP 错误 / 服务不可达 | 1 |

## 修改镜像仓结构或 index.json

- 任何变更先改 `docs/mirror-spec.md`，`schema_version` 破坏性变更时递增。
- 镜像仓内容只能由同步脚本生成，禁止在镜像仓手工提交。

## 文档同步规则

| 变更类型 | 必须同步的文档 |
|---|---|
| 命令/参数/默认行为 | `cli-spec.md` |
| 模块职责/分层/数据流 | `architecture.md` |
| 镜像布局/索引/同步行为 | `mirror-spec.md` |
| 进度变化 | `AGENTS.md` 开发进度 |

## 测试与提交

- `core/` 纯逻辑必须有单测；命令层用假注入测；不测真实网络。
- 提交前：`uv run --with pytest python -m pytest tests/ -q`。
- commit message 用英文，前缀 `feat:` / `fix:` / `docs:` / `test:` / `chore:`。
- `spec/` 为本地目录，已通过 `.git/info/exclude` 排除；克隆新环境后如需保留该约定，重新执行 `echo "/spec/" >> .git/info/exclude`。
