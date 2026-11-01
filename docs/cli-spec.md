# CLI 规范

Learnova CLI 的行为规范。实现架构见 [architecture.md](architecture.md)。

## 1. 运行形态

- Python 3.10+，纯标准库，零第三方运行时依赖。
- 以 agent skill 形式分发（`npx skills` 安装），SKILL.md 指导 agent 调用脚本。
- 分发目录：`skills/learnova-cli/`。
- 入口：`skills/learnova-cli/scripts/learnova.py`；Windows 提供 PowerShell 薄入口，POSIX 提供 sh 薄入口。
- 脚本之间无隐式共享状态，必要信息通过 `~/.learnova/` 下的本地文件交换。

## 2. 命令面

```
learnova-cli init                                   # 首次初始化引导（幂等）
learnova-cli search <关键词> [--tag t] [--sort popular|newest|likes] [--offline]
learnova-cli info <技能>                            # 元数据 + 版本列表 + README
learnova-cli install <技能>[@版本] [--project] [--agent a] [--force]
learnova-cli list [--project]
learnova-cli uninstall <技能> [--project]
learnova-cli upgrade [<技能>|--all]
learnova-cli self-update [--check]
learnova-cli login | logout | whoami
learnova-cli publish <目录> --version x.y.z --changelog "..."
learnova-cli status [<技能>]                        # 提交版本的审核状态
learnova-cli config [get] | config set <键> <值>
```

全局参数：`--json` 结构化输出。

### 默认行为

| 场景 | 约定 |
|---|---|
| 安装目标 | 默认用户级，装到**所有检测到的 agent**；`--project` 装项目级；`--agent` 限定目标 |
| 同名冲突 | 默认报错，显式 `--force` 才覆盖 |
| 升级 | 仅当索引中存在更高版本号才重装；同版本内容不变 |
| 已安装清单 | 不维护集中清单，`list`/`uninstall`/`upgrade` 扫描 agent skills 目录及轻量来源/版本记录 |
| Scope 配置 | `install_scope` **只决定 install 的默认层级**；`list`/`uninstall`/`upgrade` 默认始终操作用户级，只有显式 `--project` 才操作项目级 |
| 交互 | 默认非交互；`login`、`init` 及 `publish` 的版本号/changelog 由用户在终端亲自确认，agent 不代输凭证 |

## 3. agent 适配

映射表为数据而非代码分支，新增 agent 即新增一行。

| agent | 检测依据（目录存在） | 用户级 skills | 项目级 skills |
|---|---|---|---|
| claude | `~/.claude/` | `~/.claude/skills/` | `.claude/skills/` |
| opencode | `~/.agents/` | `~/.agents/skills/` | `.agents/skills/` |
| codex | `~/.codex/` | `~/.codex/skills/` | `.agents/skills/` |

项目级路径以当前工作目录为根。opencode 项目级路径实现时以实测为准。

## 4. 配置与本地存储

存放于 `~/.learnova/`：

| 文件 | 内容 | 权限 |
|---|---|---|
| `config.json` | 下表配置项 | 600 |
| `auth.json` | 登录凭证（refresh token 等） | 600 |
| `index_cache.json` | 社区索引离线缓存 | 600 |

写盘一律临时文件 + 原子替换。

### 配置项

| 键 | 默认值 | 说明 |
|---|---|---|
| `backend_url` | `https://ta.njunova.com` | 内置后端 API 根地址，用户配置可覆盖；旧版配置中的空值按默认地址处理 |
| `mirror_repo_url` | `https://git.nju.edu.cn/learnova/skills-mirror` | git 镜像仓地址；用户配置可覆盖 |
| `telemetry` | `auto`（跟随登录态） | `off` 时登录也不上报 |
| `telemetry_endpoint` | 空 | 独立安装事件接口的同源绝对路径；部署提供接口后配置，未配置不发送 |
| `agents` | `auto`（按目录检测） | 可限定为子集，如 `["claude", "codex"]` |
| `install_scope` | `user` | 默认安装层级 |
| `index_cache_ttl` | `86400` | 离线缓存有效期（秒） |

## 5. 检索策略

- 默认内置正式后端地址；有可用登录凭证时优先调后端 API，未登录时直接使用镜像源。旧版 `init` 保存的空 `backend_url` 在运行时使用内置默认值，非空自定义地址始终优先。
- 后端不可达时降级镜像源；镜像源优先使用未过期的本地 index 缓存，需要时联网刷新。
- `search --offline` 为**严格离线**：只读取已有 `index_cache.json`，不访问后端或 GitLab；没有缓存时直接报错。
- 索引缓存绑定产生它的镜像地址；切换镜像时不得使用旧源缓存。旧格式无来源缓存可用于原默认镜像兼容读取，切换到其他镜像须重新获取。
- 后端连接、超时、服务端错误或认证不可用时可降级；技能或版本不存在、非法响应和包校验失败须保留对应错误，不以降级掩盖。
- 镜像检索按 name/display_name/description/tags 过滤，按 likes/downloads 快照排序。
- 在线源使用镜像 `community_id → dir` 映射保留稳定目录身份，不根据当前名称猜测重名后缀。尚未进入镜像的在线结果暂不列为可安装条目；`total` 为有映射结果数，可附 `backend_total`、`unmapped_total` 说明实时来源中的未映射数量。
- 使用缓存时输出须标注 `generated_at` 并提示数据可能陈旧；缓存超过 `index_cache_ttl` 同样提示。

## 5.1 Skill 目录安全边界

- CLI 接受的技能目录标识必须是单个相对路径段；禁止空值、`.`、`..`、`/`、`\\` 与 NUL。
- 所有安装、覆盖与删除目标在执行文件操作前必须确认仍位于对应 skills 根目录内。
- 镜像 archive 只接受普通文件与目录；拒绝绝对路径、路径穿越、符号链接/硬链接及越出目标目录的成员。
- 下载后的技能包必须包含可解析且带 `name` 的 `SKILL.md`；若 `SKILL.md` 自带 `version`，其值必须与 index 中所选版本一致。
- Learnova 安装时在技能目录写入 `.learnova-install.json` 记录实际 `dir/version`，不要求第三方 Skill 为 Learnova 修改自身 frontmatter；`list` 优先使用该 sidecar；旧包仍可从 `SKILL.md` 读取版本，但来源未知的包不会自动升级。用户编辑不改变已记录的安装版本；来源扫描不递归读取内容。

## 6. 遥测

- 只收 `install` 事件：技能目录名、版本、时间戳。
- 仅登录状态下上报；未登录完全静默。接受「下载量 ≠ 真实使用量」。
- 关闭优先级：环境变量 `LEARNOVA_TELEMETRY=0` > `config telemetry=off` > 默认（跟随登录态）。
- 工程要求：fire-and-forget，短超时，失败静默，永不阻塞或失败安装主流程。
- 事件接口只接收 `{event:"install",skill,version,ts}`，需登录认证及防重放请求头。客户端通过 `telemetry_endpoint` 显式指定同源路径，不猜测线上路由，不以会修改技能库的安装接口代替统计接口。
- 实际发送由独立短命工作进程完成，CLI 不等待网络，退出不会中断已启动发送；工作进程无标准输出和错误输出，再次检查开关与登录态，不持久化事件队列。
- 工作进程仅通过私有内存管道接收本次命令已有的访问凭证；凭证不出现在参数、环境或事件文件中。后台不得刷新、保存或清除登录状态，无可用访问凭证或认证失败时放弃事件，避免与下一条命令竞争刷新凭证。
- 计数资源通过查询参数 `community_id` 定位，仅从当前镜像已有缓存中精确匹配目录与版本；缓存缺失、错源、损坏或映射不唯一时放弃事件，不联网补映射。查询参数不增加事件正文字段，也不根据名称猜测技能身份。配套后端路径为 `/community/install-events`，需由部署显式启用。

## 7. 发布链路

```
login（用户亲自执行）
  → publish <目录>：本地打 zip → 上传为仓库条目 → 提交发布（version + changelog）
  → 进入社区审核流（与网页端一致）
  → status 查询审核状态与驳回备注
```

- 发布复用后端现有上传/发布接口；审核状态与驳回备注优先复用现有查询与审核记录接口，接入前核对实际契约和部署版本。
- 发布成功后技能内容经审核上架，由镜像同步进入 git 仓，CLI 用户即可检索安装。

### 账号与初始化

`login` 由用户在真实终端输入邮箱和密码，不接受密码命令行参数；非终端调用返回 `BAD_USAGE` 和亲自登录的提示。登录使用后端当前 access token 与 refresh Cookie 协议；凭证仅保存于权限 600 的 auth.json，并绑定后端 origin。刷新成功原子保存轮换凭证，后端地址改变时不得向新 origin 发送旧凭证。输出不含 token、Cookie 或密码。

CLI 使用已有账号，当前不提供账号注册命令。

`whoami` 保留 `logged_in`、`username` 字段；无凭证时返回未登录，有凭证时查询当前账号。`logout` 尝试吊销后清除本地凭证；后端不可达也允许完成本地退出，保留 `ok`、`message` 字段。

`init` 检测 agent 并展示配置，用户终端确认后保存；已有有效配置可重复使用，不覆盖显式配置。非交互环境不等待输入，返回 `status=needs_input`、`kind=configuration`、`questions`；已完成初始化返回配置摘要。默认镜像无需额外配置，登录不作为镜像使用的前置条件。

发布使用当前目录的安全 ZIP 与现有上传/发布接口，显式提供的 `--version` 和 `--changelog` 即为本次确认，不追加重复终端确认。成功结果表达已提交审核，不等于已上架。`status` 查询当前账号的提交及审核备注，不依赖私有安装清单。

发布和构建打包均排除隐藏文件/目录与已知凭证、管理文件，避免携带本机认证状态；若成品引用被排除文件，须先整理为可公开资源再打包。发布保留原始 SKILL.md，允许常见 YAML 字符串、行尾注释和嵌套 metadata；最终格式及资源类型仍受后端规则约束。

安装元数据只读取顶层字段，嵌套 metadata 的同名键不得覆盖 name/version。发布与构建使用同一凭证排除规则，包括 tokens.json、secrets.yaml、service_account.json 和 .pem/.key/.p12/.pfx 等已知凭证文件。

认证及发布请求由账号服务编排，net 提供可选响应头、显式 Content-Type 与空响应支持；需要防重放的写请求附随机 nonce 和时间戳。Authorization 与 Cookie 均不得跟随跨 origin 重定向。

项目级 `list`、`uninstall` 和 `upgrade` 合并所选 agent 的项目目录与 `.agents/skills/`，按物理路径去重；无 agent 对应统一目录时标记为 `project`。用户级默认范围和 install 目标不变。清单新增 `source`：有效且目录名匹配的 `.learnova-install.json`（含原有 dir/version 格式）为 `community`，优先于构建记录；否则 schema 1 或 2、source 为 `local-build` 的 `.learnova-build.json` 表示 `local-build`，其余为 `unknown`。社区升级只处理物理目录中来源为 `community` 的副本，跳过其他来源；旧 frontmatter 包仍可查看和卸载，需显式 `install --force` 才能建立社区来源。

## 本地文档构建

常规宿主流程使用 `build start <input> [--preset mineru] [--name NAME] [--output DIR] [--mode agent|auto] [--project-root DIR]`。它先做只读预检查，再创建任务并自动推进到必要的语义判断或完成；预检查未通过时返回 `status=needs_input`、`preflight`，不创建半成品任务。`--preset mineru` 表示明确选择官方云解析，会一次性保存项目解析配置，只保存环境变量名。

`build tasks [--limit N] [--project-root DIR]` 返回项目任务的公开摘要（默认 20 条，最多 100），不暴露原文、图片数据或私有断点结构。

`build doctor <input> [--mode agent|auto] [--project-root DIR]` 只检查输入、可选依赖、解析配置及当前进程能否看到所引用环境变量；不调用服务，不验证 Token 有效性，不输出密钥。其他终端中 export 的变量不保证对此进程可见。

`build work <task-id> [--batch-size N] [--max-steps N] [--project-root DIR]` 自动处理机械阶段，默认一次提供最多 8 个分块的语义任务，N 为 1–16；上下文预算可能缩小批次，自动模型模式最多每批 4 块。`split_plan` 附 `proposal`（合法切点建议，Agent 可接受或修改）；无合法切点的超大块自动保留并标注，不要求 Agent 编写切片脚本。`chunk_metadata_batch` 的 scope 为 `{ids:[块ID]}`，request.chunks 包含每块范围、标题和有限原文采样；result 为 `{chunks:{块ID:{title,summary,keywords}}}`，必须覆盖这一批且整批校验通过才保存。

`build submit <task-id> (--file JSON文件|--data JSON) --continue [--batch-size N] [--max-steps N]` 保存判断后继续推进至下一个语义请求或完成。任务结果附 `summary`，含分块数、已完成/待完成语义块数；达到推进上限时附 `next_action=work`。只需为语义请求提供结果，不读取或改写私有 job.json，不重算 seals，不改原文绕过校验。原有 create/next/run/submit 默认行为保留，供底层调用及旧客户端使用。

`build config preset mineru [--project-root DIR] [--user]` 原子保存官方 MinerU 解析设置；也可 `build reconfigure <task-id> --preset mineru` 显式更新已有活动任务。预设为 remote、官方 endpoint、默认密钥环境变量名及 OCR 开启，不含任何密钥值。授权使用相应解析方式后由 Agent 执行，无须让用户填写配置 JSON；平台拒绝动作时报告实际原因，不能通过手写文件绕过拒绝。

`build config set --set KEY=VALUE [--set KEY=VALUE ...]` 支持一次原子保存多项非敏感配置；保留原单键形式。模型 API 为可选项，显式 `--mode auto` 时 CLI 自行处理包括批量语义判断在内的完整流程；不启用时由宿主提供判断。`progress` 可包含经数值检查的服务已处理页数和固定服务状态，不把服务原始响应或签名 URL 放入任务。上传阶段被拒绝使用 `BUILD_PARSER_UPLOAD_FAILED`，与解析 API 的认证失败区分。结构扫描规则变化可复用未变的解析文本，仅重新切片。

`build create <input> [--project-root DIR] [--name NAME] [--output DIR] [--mode agent|auto] [--parser remote] [--force]` 创建任务。项目根默认为当前目录；默认成品位于 `.agents/skills/<name>`。支持 Markdown 与 PDF，知识参考型 Skill 之外的类型不在范围内。

`build next|status|pause <task-id> [--project-root DIR]` 推进、查看或暂停；`build read <task-id> [--start N] [--end N] [--limit N]` 分页读取标准 Markdown；`build submit <task-id> (--file JSON文件|--data JSON)` 提交结果；`build resume <task-id> [--from parsing|chunking|indexing|finalize] [--mode agent|auto] [--force]` 恢复；`build run <task-id> [--mode agent|auto] [--until STAGE] [--max-steps N]` 循环推进；`build package <task-id>` 显式打包并返回发布指导，不上传。上述任务命令均支持 `--project-root DIR`。

构建配置 `build config get [--project-root DIR]` 和 `build config set KEY VALUE [--project-root DIR] [--user]` 单独存放：用户级为 `$LEARNOVA_HOME/build-config.json`，项目级为 `.learnova/build-config.json`。优先级为默认值、用户、项目、显式参数；任务保存当时的非敏感快照。配置项：`parser`、`remote_endpoint`、`remote_key_env`、`llm_base_url`、`llm_model`、`llm_key_env`、`min_tokens`、`max_tokens`、`max_depth`、`read_limit`。密钥字段仅保存环境变量名。`.learnova/.gitignore` 忽略 `builds/`，保留项目配置可由用户管理。

任务状态为 pending、running、needs_input、paused、failed、completed，阶段依次为 parsing、chunking、indexing、finalize。缺服务配置返回成功结果 `status=needs_input`、`kind=configuration` 和 `questions`。判断请求包含 `schema_version=1`、`task_id`、`request_id`、`document_revision`、`stage`、`kind`、`scope`、`allowed_boundaries` 和有限上下文。提交完整请求信封并增加 `result`：`split_plan` 为 `{splits:[边界ID], protected_ranges:[[起点ID,终点ID]], atomic:false}`；`chunk_metadata` 为 `{title,summary,keywords:[字符串]}`；`skill_metadata` 为 `{name,description}`。不得提交替代原文。输入变化、阶段重置使旧请求失效；配置默认值变化不影响已有快照。输出冲突需要创建时或恢复时显式指定 `--force`。错误包括 `BUILD_TASK_NOT_FOUND`、`BUILD_STALE_REQUEST`、`BUILD_PLAN_INVALID`、`BUILD_INTEGRITY_FAILED`、`BUILD_CONFIG_INVALID`、`BUILD_INPUT_INVALID`、`BUILD_ADAPTER_UNAVAILABLE`；参数错误退出 2，其余错误退出 1，目录冲突沿用现有事务错误。

断点包含解析、切片、语义与配置依赖的完整性摘要；缺字段或摘要不匹配返回 `BUILD_INTEGRITY_FAILED`，不会继续使用损坏状态。执行失败保留最近已保存断点并将状态设为 `failed`，`error` 仅包含安全错误码和恢复提示；`resume` 后重试。`build reconfigure <task-id> --set KEY=VALUE [--set KEY=VALUE ...]` 显式修改该任务快照：解析器配置从 parsing 重置，切片阈值与模型配置从 chunking 重置，`read_limit` 只调整读取上限；切换模式不会丢弃有效结果。更新默认配置本身不会影响旧任务，`resume` 也不自动加载默认配置。

判断请求最多附 100 个合法边界、40 个标题或 10 条分块元数据，并附总数；完整列表通过 `build read <task-id> --kind boundaries|headings|chunks --start N --limit N` 分页，N 为列表序号，每页最多 100 条。boundaries 在切片请求期间仅列出该范围内合法切点；通过分页获取的边界同样可提交。默认 `--kind text` 的 start/end 是 Unicode 字符偏移，limit 受配置上限约束。分块标题和摘要最多各 512 字符，关键词最多 32 个且每个最多 128 字符。

保护范围端点使用结构表的边界 ID（`b` 加字符偏移）；当前范围的起止偏移可作为端点，分页的 boundaries 列表只包含可切分的内部边界。提交前记录候选内容摘要；若进程在成品替换后意外退出，重试仅在现有目录与该候选逐文件一致时确认完成，否则仍遵守冲突与覆盖规则。

强制替换使用每个输出目录独立加锁的私有事务记录。若进程在旧目录移入备份后终止，下一次对该输出执行构建提交时先验证事务归属并恢复旧目录，再重试；异常退出到下一次操作之间原路径可能暂时不存在。事务记录损坏、归属无法验证或路径出现符号链接时停止并报告完整性错误，保留备份供检查。

服务地址仅允许不含用户名、密码、查询参数和片段的 HTTP(S) URL；密钥通过环境变量名引用。等待环境变量的配置问题会在 `next` 或 `resume` 时重新判断；切回 agent 模式会解除不再需要的模型配置问题。

### PDF 解析契约

PDF 解析只使用一套 MinerU V4 HTTP 上传、轮询和下载流程；`parser=remote` 为兼容保留的配置名，同时适用于云端和 V4 兼容自部署服务。统一配置 `remote_endpoint` 与可选 `remote_key_env`：后者留空时不发送 API 认证，配置了环境变量名时必须能读取该变量。官方预设仍引用 `MINERU_API_KEY`。自部署服务必须实现相同 V4 协议，不自动兼容原生 V1 或 Gradio。PDF 页数与拆页仅需按需加载 `pypdf`；缺少依赖报 `BUILD_DEPENDENCY_MISSING`，不影响 Markdown 或原有命令。

新增解析配置（全部属于 parsing 的失效及摘要依赖）：

| 键 | 默认值 | 含义 |
|---|---|---|
| `parser_pages_per_part` | 200 | 保守拆页目标，不表示服务当前上限 |
| `parser_max_part_bytes` | 209715200 | 单次 PDF 上传字节上限 |
| `parser_language` | `ch` | MinerU 语言参数 |
| `parser_formula` / `parser_table` / `parser_ocr` | true / true / false | 公式、表格与 OCR 参数 |
| `remote_model_version` | `vlm` | 远端模型版本参数 |
| `parser_request_timeout` / `parser_upload_timeout` / `parser_download_timeout` | 30 / 120 / 120 | 请求、上传、下载秒数上限 |
| `parser_archive_bytes` / `parser_expanded_bytes` / `parser_member_bytes` | 104857600 / 268435456 / 67108864 | ZIP 下载、总展开、单成员字节上限 |

远端 endpoint 是服务根 URL；POST `/api/v4/file-urls/batch` 顶层发送 `enable_formula/enable_table/language/model_version`，files 内发送通用文件名、`data_id/is_ocr`。PUT 上传原始 PDF，不带 bearer；Content-Type 保持预签名要求的空值，并阻止 HTTP 库自动补入表单类型。GET `/api/v4/extract-results/batch/{batch_id}` 查询一次进度，再下载 `full_zip_url`。单次 next 只推进一个分片的一步，不循环等待云端任务。批次 ID 在上传、轮询、下载前落盘；签名 URL 只留内存。重启遇到等待上传且签名 URL 无法恢复的分片时，在 progress.warnings 和私有状态标记 `upload_resubmitted`，下一步重新提交该分片，已完成分片不重做。

Gradio 路径已移除，不再使用 SDK、独立解析进程或本地事件 ID。旧 `local_endpoint/local_api_name/local_language/local_backend/local_result_timeout` 字段只为读取旧配置、输出契约和断点摘要保留，不接受新设置，也不参与 HTTP 请求。新任务的旧 `parser=local` 配置，以及仍需执行解析的旧任务会返回 `BUILD_CONFIG_INVALID`，须配置 V4 地址后切换为 `remote`。已取得有效解析结果、处于切片或索引等后续阶段的旧任务可直接继续，不强制重解析。需要重跑解析时，再通过 `build reconfigure` 显式切换接口；不自动向其他地址重新上传。已完成历史和既有 V4 断点继续可读。

ZIP 支持 `full.md`、`markdown.md`，或唯一无歧义 Markdown；多个候选报错。所有成员只接受普通相对文件/目录，拒绝穿越、链接、设备、重复成员及展开超限。仅打包 Markdown 实际引用的图片，分片图片独立命名后再统一归一化。缺失或不安全图片失败，不丢弃原文。来自解析内容的图片抽取或校验异常统一返回固定的 `BUILD_PARSER_ARCHIVE_INVALID`，不回显引用标签或原始异常；非字符串结果 URL 返回 `BUILD_PARSER_RESPONSE_INVALID`。

私有任务的 `normalized_page_map` 保存 `status`（complete/partial/unavailable）和 `ranges:[{start,end,page}]`：范围为归一化文档的 Unicode 半开区间，page 为原 PDF 从 1 开始的页码。只接受数值范围且转换图片链接编辑后的偏移。已支持旧 `*_content_list.json` 的平铺/按页分组 `page_idx/text`，以及 `structured_content.json` / `middle_json.json` 中带 `page_idx` 的 pages/pdf_info 与 blocks/para_blocks 的 text 或 lines.spans.content。无歧义原样文本匹配才建立范围，不推断整个页面的缺失内容。未知结构、重复文本及无法对齐的条目保留私有元数据并报告 partial/unavailable；最终来源记录不包含页映射、文件清单、指纹、服务原始 JSON、路径或凭证。

自动构建使用 `llm_base_url`、`llm_model`、`llm_key_env`；后者仅保存环境变量名。模型响应异常返回 `BUILD_MODEL_RESPONSE_INVALID`，网络错误返回 `BUILD_MODEL_REQUEST_FAILED`，结果仍需通过 `BUILD_PLAN_INVALID` 校验。服务不记录密钥或原始错误内容。`build run --until chunking` 在解析完成并进入切片阶段时停止；`build next` 每次最多即时推进一次解析轮询。

`build resume <task_id> --force --project-root <PROJECT>` 显式授权该任务覆盖现有目标，保留未重置的解析和判断结果；省略 `--force` 不撤销此前授权。创建后修改默认配置不会改变任务快照，配置问题应通过 `build reconfigure` 修改快照；仅设置已引用的环境变量时可直接 next 或 resume。

### 成品完整性与超大块提示

生成候选必须通过私有原文切片、覆盖与保护范围校验。交付后允许直接编辑目录，`package` 使用独立的纯校验器检查当前 SKILL.md 的 frontmatter、合法 name、非空 description 及本地参考，失败不生成新 ZIP。

任务状态结果新增 `warning_count`（带超大提示的块数）；通过 `read --kind chunks --start N --limit N` 分页获取每块新增的 `tokens`（字符数除以 4 向上取整，仅近似）和 `warnings`。允许的固定原因只有 `oversized:atomic`（完整保留的不可拆范围）、`oversized:no_cut`（未选择有效切点）、`oversized:max_depth`（达到递归上限）。tokens/warnings 留在私有任务，SKILL.md 导航仅静态标注受影响参考及原因；不往原文参考插入提示。

活动任务的成品渲染规则版本独立于判断规则：渲染变化只重置 finalize，保留解析和已接受判断；判断规则变化仍从 chunking 重置。解析 checkpoint 的源 Markdown 和归一化原文允许普通查询链接且精确保留；传输状态仅保存安全任务标识，不保存上传/下载 URL，认证信息检查仍生效。

### 成品与完成历史

新 `.learnova-build.json` 仅为 `{"schema_version":2,"source":"local-build","version":null}`，自动写入。旧 schema 1 来源字段仍可识别，读取时不迁移或验证历史摘要；匹配的社区 dir/version sidecar 优先。目录和 SKILL.md 提供名称，未发布本地 Skill 没有社区版本，list 保持既有字符串类型并返回空 version。

completed 是历史状态：status/next/普通 resume 不读取成品、原始输入/图片或判断规则，不因编辑、卸载、移动输入或规则升级而重开任务。显式 resume --from 才重新检查适用的私有输入与规则，并保留冲突/force 语义。完成后的普通 resume 可更新明确传入的 mode/force，但不重开构建；改变会使阶段失效的任务配置前，也须先 resume --from。活动构建与中断提交恢复仍验证私有断点及候选摘要。

build package 读取当前成品目录，校验名称/描述、普通安全路径、当前本地参考和图片；接受新增文件及编辑，不要求来源记录或原快照。ZIP 排除 .learnova-install.json、.learnova-build.json、.learnova/、版本库、凭证与管理临时文件。成品删除时报告打包错误，不修改完成历史或自动重建。不上传、不更改安装版本。

`build read` 始终读取私有任务快照，用于构建与排查；日常加载 Skill 应读取当前成品文件，不能用旧快照覆盖用户编辑。

`build run` 达到步数上限时返回最后一次已保存操作之后的当前状态，不返回已提交的判断请求。同一技能的多个社区来源安装副本在升级时作为一个事务提交；目标之一失败则回滚本次该技能全部目标，来源为本地构建或未知的副本仍跳过。

普通自动判断默认请求最多 2048 输出 token，批量元数据最多 8192；服务明确返回 `finish_reason=length` 且预算尚未到 8192 时，允许提高后重试一次，仍受总请求次数与响应大小限制。达到上限后再次截断返回既有格式错误，不接受不完整判断。
