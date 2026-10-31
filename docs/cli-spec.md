# CLI 规范

Learnova CLI 的行为规范。实现架构（模块划分与设计模式）另行约定后补充。

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
| `backend_url` | 发行时内置 | 后端 API 根地址 |
| `mirror_repo_url` | `https://git.nju.edu.cn/learnova/skills-mirror` | git 镜像仓地址；用户配置可覆盖 |
| `telemetry` | `auto`（跟随登录态） | `off` 时登录也不上报 |
| `agents` | `auto`（按目录检测） | 可限定为子集，如 `["claude", "codex"]` |
| `install_scope` | `user` | 默认安装层级 |
| `index_cache_ttl` | `86400` | 离线缓存有效期（秒） |

## 5. 检索策略

- 最终形态默认调后端 API（与网页端一致的实时检索）；当前后端数据源尚未接入，未配置后端时直接使用镜像源。
- 后端不可达时降级镜像源；镜像源优先使用未过期的本地 index 缓存，需要时联网刷新。
- `search --offline` 为**严格离线**：只读取已有 `index_cache.json`，不访问后端或 GitLab；没有缓存时直接报错。
- 镜像检索按 name/display_name/description/tags 过滤，按 likes/downloads 快照排序。
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

## 7. 发布链路

```
login（用户亲自执行）
  → publish <目录>：本地打 zip → 上传为仓库条目 → 提交发布（version + changelog）
  → 进入社区审核流（与网页端一致）
  → status 查询审核状态与驳回备注
```

- 发布复用后端现有上传/发布接口；审核状态查询依赖后端提供的 publish-status 接口。
- 发布成功后技能内容经审核上架，由镜像同步进入 git 仓，CLI 用户即可检索安装。

项目级 `list`、`uninstall` 和 `upgrade` 合并所选 agent 的项目目录与 `.agents/skills/`，按物理路径去重；无 agent 对应统一目录时标记为 `project`。用户级默认范围和 install 目标不变。清单新增 `source`：有效且目录名匹配的 `.learnova-install.json`（含原有 dir/version 格式）为 `community`，优先于构建记录；否则 schema 1 或 2、source 为 `local-build` 的 `.learnova-build.json` 表示 `local-build`，其余为 `unknown`。社区升级只处理物理目录中来源为 `community` 的副本，跳过其他来源；旧 frontmatter 包仍可查看和卸载，需显式 `install --force` 才能建立社区来源。

## 本地文档构建

`build create <input> [--project-root DIR] [--name NAME] [--output DIR] [--mode agent|auto] [--parser remote|local] [--force]` 创建任务。项目根默认为当前目录；默认成品位于 `.agents/skills/<name>`。支持 Markdown 与 PDF，知识参考型 Skill 之外的类型不在范围内。

`build next|status|pause <task-id> [--project-root DIR]` 推进、查看或暂停；`build read <task-id> [--start N] [--end N] [--limit N]` 分页读取标准 Markdown；`build submit <task-id> (--file JSON文件|--data JSON)` 提交结果；`build resume <task-id> [--from parsing|chunking|indexing|finalize] [--mode agent|auto] [--force]` 恢复；`build run <task-id> [--mode agent|auto] [--until STAGE] [--max-steps N]` 循环推进；`build package <task-id>` 显式打包并返回发布指导，不上传。上述任务命令均支持 `--project-root DIR`。

构建配置 `build config get [--project-root DIR]` 和 `build config set KEY VALUE [--project-root DIR] [--user]` 单独存放：用户级为 `$LEARNOVA_HOME/build-config.json`，项目级为 `.learnova/build-config.json`。优先级为默认值、用户、项目、显式参数；任务保存当时的非敏感快照。配置项：`parser`、`remote_endpoint`、`remote_key_env`、`local_endpoint`、`llm_base_url`、`llm_model`、`llm_key_env`、`min_tokens`、`max_tokens`、`max_depth`、`read_limit`。密钥字段仅保存环境变量名。`.learnova/.gitignore` 忽略 `builds/`，保留项目配置可由用户管理。

任务状态为 pending、running、needs_input、paused、failed、completed，阶段依次为 parsing、chunking、indexing、finalize。缺服务配置返回成功结果 `status=needs_input`、`kind=configuration` 和 `questions`。判断请求包含 `schema_version=1`、`task_id`、`request_id`、`document_revision`、`stage`、`kind`、`scope`、`allowed_boundaries` 和有限上下文。提交完整请求信封并增加 `result`：`split_plan` 为 `{splits:[边界ID], protected_ranges:[[起点ID,终点ID]], atomic:false}`；`chunk_metadata` 为 `{title,summary,keywords:[字符串]}`；`skill_metadata` 为 `{name,description}`。不得提交替代原文。输入变化、阶段重置使旧请求失效；配置默认值变化不影响已有快照。输出冲突需要创建时或恢复时显式指定 `--force`。错误包括 `BUILD_TASK_NOT_FOUND`、`BUILD_STALE_REQUEST`、`BUILD_PLAN_INVALID`、`BUILD_INTEGRITY_FAILED`、`BUILD_CONFIG_INVALID`、`BUILD_INPUT_INVALID`、`BUILD_ADAPTER_UNAVAILABLE`；参数错误退出 2，其余错误退出 1，目录冲突沿用现有事务错误。

断点包含解析、切片、语义与配置依赖的完整性摘要；缺字段或摘要不匹配返回 `BUILD_INTEGRITY_FAILED`，不会继续使用损坏状态。执行失败保留最近已保存断点并将状态设为 `failed`，`error` 仅包含安全错误码和恢复提示；`resume` 后重试。`build reconfigure <task-id> --set KEY=VALUE [--set KEY=VALUE ...]` 显式修改该任务快照：解析器配置从 parsing 重置，切片阈值与模型配置从 chunking 重置，`read_limit` 只调整读取上限；切换模式不会丢弃有效结果。更新默认配置本身不会影响旧任务，`resume` 也不自动加载默认配置。

判断请求最多附 100 个合法边界、40 个标题或 10 条分块元数据，并附总数；完整列表通过 `build read <task-id> --kind boundaries|headings|chunks --start N --limit N` 分页，N 为列表序号，每页最多 100 条。boundaries 在切片请求期间仅列出该范围内合法切点；通过分页获取的边界同样可提交。默认 `--kind text` 的 start/end 是 Unicode 字符偏移，limit 受配置上限约束。分块标题和摘要最多各 512 字符，关键词最多 32 个且每个最多 128 字符。

保护范围端点使用结构表的边界 ID（`b` 加字符偏移）；当前范围的起止偏移可作为端点，分页的 boundaries 列表只包含可切分的内部边界。提交前记录候选内容摘要；若进程在成品替换后意外退出，重试仅在现有目录与该候选逐文件一致时确认完成，否则仍遵守冲突与覆盖规则。

强制替换使用每个输出目录独立加锁的私有事务记录。若进程在旧目录移入备份后终止，下一次对该输出执行构建提交时先验证事务归属并恢复旧目录，再重试；异常退出到下一次操作之间原路径可能暂时不存在。事务记录损坏、归属无法验证或路径出现符号链接时停止并报告完整性错误，保留备份供检查。

服务地址仅允许不含用户名、密码、查询参数和片段的 HTTP(S) URL；密钥通过环境变量名引用。等待环境变量的配置问题会在 `next` 或 `resume` 时重新判断；切回 agent 模式会解除不再需要的模型配置问题。

### PDF 解析契约

`build` 按 `parser=remote|local` 惰性选择 MinerU 适配器。PDF 页数与拆页需要 `pypdf`，本地服务另需 `gradio_client`；缺少依赖报 `BUILD_DEPENDENCY_MISSING`，提示用 `uv pip install --python <运行解释器> pypdf gradio_client` 安装所需包，不影响 Markdown 或原有命令。

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
| `local_api_name` | `/convert_to_markdown_stream` | 部署公开的 Gradio API 名称 |
| `local_language` | `ch (Chinese, English, Chinese Traditional)` | 本地部署语言下拉选项的完整值，与远端短代码独立 |
| `local_backend` | `pipeline` | 本地 backend 参数 |
| `local_result_timeout` | 300 | 本地结果等待秒数上限 |

远端 endpoint 是服务根 URL；POST `/api/v4/file-urls/batch` 顶层发送 `enable_formula/enable_table/language/model_version`，files 内发送通用文件名、`data_id/is_ocr`。PUT 上传原始 PDF，不带 bearer；GET `/api/v4/extract-results/batch/{batch_id}` 查询一次进度，再下载 `full_zip_url`。单次 next 只推进一个分片的一步，不循环等待云端任务。批次 ID 在上传、轮询、下载前落盘；签名 URL 只留内存。重启遇到等待上传且签名 URL 无法恢复的分片时，在 progress.warnings 和私有状态标记 `upload_resubmitted`，下一步重新提交该分片，已完成分片不重做。

本地部署必须匹配显式 API：`file_path=handle_file(part.pdf), end_pages=分片页数, formula_enable, table_enable, language, backend, url=''`。通过 SDK submit/result 调用，独立进程限制请求与结果等待，隔离 SDK 日志；结果需含唯一一个 ZIP 文件，SDK 下载路径限制在临时结果目录。若部署输出签名不同，应配置 API 名称或使用匹配服务，不能冒充成功。SDK 如提供安全 event_id，立即保存后才等待结果；SDK 无跨进程恢复协议，未完成的本地任务重启会显式记录 `local_resubmitted`。已完成分片仍复用。

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
