# 维护手册

加新功能或修改行为时按本手册执行。总原则：**先改设计文档，再改代码**；代码必须与 `cli-spec.md` / `architecture.md` / `mirror-spec.md` 保持一致。

## 加一个新命令

1. 在 `docs/cli-spec.md` §2 命令面中登记命令、参数与默认行为。
2. 新建 `skills/learnova-cli/scripts/cli/commands/<name>.py`，实现 `register(sub)` 与 `handler(args, ctx) -> dict`。
3. 在 `skills/learnova-cli/scripts/cli/commands/__init__.py` 的 `MODULES` 注册一行。
4. handler 内：解析参数 → 委托对应服务 → 返回 dict。数据源选择走 `sources/resolver.py`，目录事务走 `engine.py`，状态读写走 `store.py`。
5. 需要新 error code 时，在本文「错误码登记表」登记。
6. 加测试：命令逻辑用假 ctx + FakeSource 注入测；纯逻辑抽到 `core/` 单测。

禁止：handler 里 print、直接调 urllib、直接读写 `~/.learnova/`（走 `store.py`）、直接操作文件系统（走 `engine.py`）。

## 加一个新 agent 适配

1. 在 `skills/learnova-cli/scripts/agents.py` 的 `AGENTS` 表中加一行（`detect_dir` / `global_skills` / `project_skills`）。
2. 同步更新 `docs/cli-spec.md` §3 的映射表。
3. 目标路径必须在真实环境实测确认后在 PR 描述中说明依据。

## 加一个新数据源

1. 在 `skills/learnova-cli/scripts/sources/` 新建模块，实现 `SkillSource` 协议（`search` / `info` / `fetch`）。
2. 在 `sources/resolver.py` 注册并明确其在回退链中的位置。
3. 更新 `docs/architecture.md` §3 与 `docs/cli-spec.md` §5 检索策略。

## 维护账号、发布与遥测

- 登录交互、刷新 Cookie、请求 nonce 和来源绑定统一由 `auth.py` 管理。访问凭证只留内存；后台遥测使用 `allow_refresh=False`，不得保存、清除或刷新账号状态。
- 发布目录扫描、上传、提交与审核查询统一由 `publishing.py` 管理。每次上传创建新条目，不按同名技能猜测远端发布目标；提交成功只代表进入审核。
- 发布与构建打包共用 `core/package_paths.py`，修改私有文件过滤时同时检查两个入口。普通 YAML 的嵌套元数据不能覆盖顶层名称或版本。
- 遥测仅在显式配置同源端点并满足登录及收集条件时发送。稳定映射只读现有缓存，缺映射或认证失败即丢弃事件，不额外联网补映射、不影响安装。

## 修改镜像生产端

镜像生产端在 `mirror/`，不属于 `skills/learnova-cli/` 的用户侧安装包，也不放进 Learnova 主仓。

- `source.py` 只读 Learnova SQLite 与社区归档。
- `repository.py` 收敛 Git 工作树、commit、历史恢复与 push。
- `index.py` 负责 `index.json` 生成。
- `sync.py` 只负责 one-shot 编排和配置入口，不实现常驻调度循环。
- `deploy/` 只放 systemd、cron 等薄适配，不能复制同步逻辑。
- `mirror/` 必须保持可独立部署；运行时不得依赖 consumer CLI、tests 或仓库根目录其他文档。开发规范仍维护在 `docs/mirror-spec.md`，提交前同步更新运行时副本 `mirror/SPEC.md`，测试会检查两者一致。

改动 producer 时至少运行：

```bash
python -m unittest tests.test_mirror_sync -v
```

测试使用临时 SQLite、临时归档和真实临时 Git repo，不依赖生产服务器。涉及 Git 历史模型或 index schema 的改动，先更新 `mirror-spec.md`。

## 修改输出契约

- JSON 字段、error code、退出码发布后**只增不改不删**。必须变更时，提升 schema 版本并在 `docs/cli-spec.md` 中注明迁移说明。
- 人类可读输出不属于契约，可自由调整。

## 错误码登记表

新 error code 在此登记（code、含义、退出码）：

| code | 含义 | 退出码 |
|---|---|---|
| `BUILD_PLAN_INVALID` | 构建切分方案或语义元数据非法 | 2 |
| `BUILD_INTEGRITY_FAILED` | 构建产物或来源覆盖校验失败 | 1 |
| `NOT_IMPLEMENTED` | 保留兼容错误码，旧版本能力未实现 | 1 |
| `SKILL_NOT_FOUND` | 索引中不存在指定技能 | 1 |
| `VERSION_NOT_FOUND` | 索引中不存在指定版本 | 1 |
| `SKILL_NOT_INSTALLED` | 本地 agent 目录中未安装指定技能 | 1 |
| `NO_AGENT_FOUND` | 未检测到任何 agent 目标目录 | 1 |
| `MIRROR_NOT_CONFIGURED` | 走镜像源但未配置 `mirror_repo_url` | 1 |
| `MIRROR_ARCHIVE_INVALID` | 镜像 archive 内容结构不符合预期 | 1 |
| `MIRROR_INDEX_INVALID` | 镜像 index.json schema/字段非法 | 1 |
| `OFFLINE_CACHE_MISS` | 严格离线检索时没有本地索引缓存 | 1 |
| `SKILL_PACKAGE_INVALID` | 下载的技能包缺少/包含非法 SKILL.md 或版本不一致 | 1 |
| `LOCAL_STATE_INVALID` | 本地 JSON 状态损坏或配置值非法 | 1 |
| `HTTP_RESPONSE_TOO_LARGE` | 网络响应超过 CLI 安全上限 | 1 |
| `INTERNAL_ERROR` | 未预期的内部错误（Router 最终兜底） | 1 |
| `BAD_USAGE` / `BAD_CONFIG_KEY` | 参数或配置项错误 | 2 |
| `INVALID_SKILL_DIR` | 技能目录标识不是安全的单路径段 | 2 |
| `SKILL_CONFLICT` | 目标目录已存在同名技能 | 3 |
| `UNAUTHORIZED` | 凭证失效或未登录 | 4 |
| `HTTP_ERROR` / `UNREACHABLE` | 后端 HTTP 错误 / 服务不可达 | 1 |
| `BACKEND_NOT_CONFIGURED` | 需要账号功能但未配置后端 | 1 |
| `BACKEND_RESPONSE_INVALID` | 后端返回结构不符合客户端契约 | 1 |
| `SKILL_AMBIGUOUS` | 技能名称对应多个社区条目，无法确定稳定目录 | 2 |
| `PUBLISH_PACKAGE_INVALID` | 待发布目录元数据、文件或路径不合法 | 2 |
| `PUBLISH_FAILED` | 发布流程未能完成 | 1 |

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

实现、合并和部署分别记录，注明对应分支或版本。通用环境核查表不等于开发待办；本地测试通过不代表已上线或其他平台已实测。

## 测试与提交

- `core/` 纯逻辑必须有单测；端到端走 T1（本地 stub + 临时目录），不测真实网络。
- 账号改动覆盖 `test_auth.py`；数据源与回退覆盖 `test_backend_source.py`；发布、遥测和子进程链路分别覆盖 `test_publish.py`、`test_telemetry.py`、`test_backend_flow.py`。跨模块缺陷回归集中在 `test_completion_defects.py`。
- 提交前：`python -m unittest discover tests -v`（T0 + producer）；`python tests/t1_mirror_flow.py`（T1）。
- commit message 用英文，前缀 `feat:` / `fix:` / `docs:` / `test:` / `chore:`。
- `internal/` 为本地目录，已通过 `.git/info/exclude` 排除；克隆新环境后如需保留该约定，重新执行 `echo "/internal/" >> .git/info/exclude`。

文档构建的结构扫描、切片和产物校验保持纯函数；项目任务文件由构建 jobs 管理，正式目录提交由 engine 负责。

### 本地构建

构建命令保持薄层；`build/config.py` 按需校验独立配置并复用 store 原子 JSON 操作，`build/jobs.py` 管理项目私有任务与锁，`build/pipeline.py` 协调阶段并注入解析和判断适配器。纯文档处理在 `core/`，最终目录通过 engine 事务提交。新增结果字段或错误码需同步 CLI 规范并覆盖恢复、旧请求与损坏断点测试；禁止将实际密钥写入快照或产物。

任务快照每次写入重新计算解析、切片、语义及配置依赖摘要；读取缺失或损坏的状态必须报 `BUILD_INTEGRITY_FAILED`，不能跳过检查。配置重设按解析、切片、读取三类依赖分别失效；查询结构和语义列表按页返回，判断请求不得塞入完整大型文档结构。

目录事务在输出父目录创建权限受限的锁和事务记录；重试先持锁验证暂存目录归属并恢复中断的替换。变更此协议时覆盖旧目录移走、新目录提交前的真实进程退出，且不得削弱多目标异常回滚和备份保留语义。

### PDF 适配器维护

| code | 含义 | 退出码 |
|---|---|---|
| `BUILD_DEPENDENCY_MISSING` | 缺少惰性 PDF 依赖 pypdf，按提示使用 uv 安装 | 1 |
| `BUILD_PARSER_FAILED` | HTTP 解析服务返回失败，消息不含上游正文 | 1 |
| `BUILD_PARSER_RESPONSE_INVALID` | 解析服务回复结构、ID、Markdown 或图片不符合契约 | 1 |
| `BUILD_PARSER_ARCHIVE_INVALID` | ZIP 路径、成员类型、大小或歧义校验失败 | 1 |
| `BUILD_PARSER_TIMEOUT` | 保留兼容错误码；当前 HTTP 超时使用 HTTP_TIMEOUT | 1 |
| `BUILD_PARSER_UPLOAD_FAILED` | 预签名对象存储上传被拒绝，与解析 API Token 认证阶段区分 | 1 |
| `HTTP_TIMEOUT` | HTTP 连接/上传/响应超时 | 1 |

解析内容异常必须在 parser 边界替换成固定安全消息，不透传共享 Markdown 抽取函数的输入标签。HTTP 响应头读取前的连接断开同样翻译为 `HTTP_ERROR`，且不附带底层异常信息。

新增解析配置必须纳入 config 的集中依赖组，供 jobs seals 和 pipeline reconfigure 同时使用。HTTP 测试仅用 localhost 临时服务，pypdf 用可替换依赖边界；覆盖认证不泄露、签名 URL 不落盘、上传/轮询/下载中断、完成分片复用、ZIP 安全、页偏移和安装包内完整构建。完整 unittest 与 T1 都须保持通过。官方云端解析与自动模型已通过真实服务验收；自部署仅承诺 V4 兼容 HTTP 协议。

自动判断错误 `BUILD_MODEL_REQUEST_FAILED` 和 `BUILD_MODEL_RESPONSE_INVALID` 使用固定脱敏信息；模型方案仍由 `BUILD_PLAN_INVALID` 校验。修改随包分发的判断规则会让活动任务从切片阶段重新判断并保留解析结果；旧请求失效。完成任务保持历史状态，只有显式 resume --from 才重新验证依赖。运行中仅解析轮询间隔变化不会使解析结果失效。

轻量管理回归必须覆盖：编辑后来源/安装版本不变，扫描不递归读取；schema 2 最小记录和旧 schema 1 识别；完成后成品/输入删除与规则变化不影响历史；显式重建冲突/force 和私有断点复用；打包当前新增/编辑内容并排除管理、VCS 和凭证文件，同时拒绝损坏元数据、缺失参考/图片、不安全路径及链接。生成期精确覆盖、保护区间、私有 seals 和事务恢复测试必须保留。

统一解析器仅实现 V4 HTTP 上传、轮询与下载。旧 local_* 字段只保留配置读取、输出与历史摘要兼容，不接受新设置；旧 parser=local 任务如已有有效解析结果可继续后续阶段；仅在需要再次解析时提示显式 reconfigure，不能静默向其他服务重提交。修改默认配置项或 PARSING_KEYS 时须验证既有 V4 断点和已完成历史仍可读取。
