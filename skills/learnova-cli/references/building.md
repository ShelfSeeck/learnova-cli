# 从文档构建 Skill

构建支持 UTF-8 Markdown 和 PDF，生成知识参考型 Skill：通过索引和原文参考资料查找主题或章节、结合原文解释概念和回答问题、比较内容。其他工作流型 Skill 需要额外能力。使用前请安装并加载具备构建能力的 Learnova CLI Skill。

默认由宿主 Agent 做语义判断和流程编排，CLI 负责确定性处理；宿主模式不需要额外的 LLM 密钥。Agent 根据内容生成标题、关键词、范围及 Skill 元数据，脚本自动写入轻量来源记录，无需用户填写 JSON 或元数据表单。复用对话中明确的输入、项目、输出和有效解析配置，只询问缺少的输入、输出或解析配置。项目明确而未指定输出时，简要说明默认使用项目 `.agents/skills/<name>/` 后继续；尊重用户指定的项目根和对应 Agent 的加载目录，不要从用户全局目录推断宿主，也不要将工具安装目录作为输出或擅自修改用户级配置。项目或输出含义不明时再询问。目标已存在时默认拒绝覆盖，只有用户明确授权才加 `--force`。

首次回复示例：“我会把文档制作成可按章节查阅、结合原文回答问题的知识参考 Skill；我会处理结构和索引。请提供文档路径。”若文件、项目和解析配置已明确，则省略相应问题。

所有命令使用 `--json`，以下的 `CLI` 指安装目录的 `python <skill-dir>/scripts/learnova.py --json`；`PROJECT` 是明确的项目根目录，当前目录只有在项目上下文明确时才作为默认。任务 ID 和 JSON 信封由 Agent 管理；继续旧任务时复用对话信息，必要时仅检查相关项目的 `.learnova/builds/` 记录，确有多个可能任务时询问一次，不存在任务列表命令。

## 宿主 agent 流程

1. `build create <input.md-or-pdf> --project-root <PROJECT> [--name <name>] [--output <path>]` 返回 `task_id`。
2. `build next <task_id> --project-root <PROJECT>` 推进一次；PDF 解析进行中时稍后重试。也可 `build run <task_id> --until chunking --project-root <PROJECT>`，它在进入切片阶段时停止，即解析已完成。
3. `needs_input` 的 `request` 是版本 1 信封，含 `task_id`、`request_id`、`document_revision`、`stage`、`kind` 和 `scope`。已有任务遇到配置问题时用 `build reconfigure <task_id> --set KEY=VALUE --project-root <PROJECT>` 更新任务快照；如果只是为已引用的环境变量提供值，可直接 next 或 resume。其他请求复制完整信封并加 `result`，用 `build submit <task_id> --file <json-file> --project-root <PROJECT>` 提交。切片结果为 `{"splits":["boundary-id"],"protected_ranges":[["start-id","end-id"]],"atomic":false}`；块元数据为 `{"title":"...","summary":"...","keywords":["..."]}`；Skill 元数据为 `{"name":"sample","description":"..."}`。只提供判断，不改写原文。
4. 按需调用 `build read <task_id> --kind boundaries --start 0 --limit 100 --project-root <PROJECT>`（也可用 `headings` 或 `chunks`），用返回的 `next_start` 继续分页；`build read <task_id> --kind text --start N --end N --limit N --project-root <PROJECT>` 读取 Unicode 字符范围。请求中的结构只是预览，不能当作完整列表。
5. 循环推进和提交直至 `completed`。查看 `build status <task_id> --project-root <PROJECT>`，检查成品及 `warning_count`；计数非零时用 `read --kind chunks` 分页查看 `tokens` 和 `warnings`（`oversized:atomic`、`oversized:no_cut`、`oversized:max_depth`）。这些原因保留在私有任务和静态参考导航中；校验失败时保留任务并处理原因，不忽略错误。

## 配置和自动模式

`build config set <key> <value> --project-root <PROJECT>` 保存项目配置，`--user` 保存用户配置；创建时快照，不会自动更新已有任务。PDF 远程模式需要 `remote_endpoint` 和 `remote_key_env`；本地模式需要 `local_endpoint`。PDF 拆页需要 `uv pip install pypdf`；本地 Gradio 还需要 `uv pip install gradio_client`，请在操作员选定的虚拟环境执行。可选依赖缺失不影响社区检索安装。

自动模式另需 `llm_base_url`（接口基地址）、`llm_model`、`llm_key_env`（环境变量名）。由操作员在终端设置该环境变量，绝不把密钥写入配置或提交结果。创建时加 `--mode auto`，然后 `build run <task_id> --project-root <PROJECT>`。模型只返回判断或请求额外分页；网络或非法响应失败后检查配置，`build resume <task_id> --project-root <PROJECT>` 再运行，不会静默切换模式。

远端解析使用 MinerU v4 的上传、轮询和结果 ZIP 协议；`remote_endpoint` 为服务 API 根地址，不是单次签名上传链接。本地解析通过 Gradio 的 `local_api_name` 调用，默认 `/convert_to_markdown_stream`；服务须提供相容签名。示例配置（地址及模型为占位符）：

```bash
CLI build config set remote_endpoint https://parser.example.invalid --project-root <PROJECT>
CLI build config set remote_key_env MINERU_API_KEY --project-root <PROJECT>
CLI build config set llm_base_url https://model.example.invalid/v1 --project-root <PROJECT>
CLI build config set llm_model example-model --project-root <PROJECT>
CLI build config set llm_key_env MODEL_API_KEY --project-root <PROJECT>
```

其中 `CLI` 须替换为前文的 Python 入口。`build config get --project-root <PROJECT>` 查看有效配置；页面分片、语言、OCR、超时和大小上限均可配置。解析进行中时 `next` 只轮询一次，`run` 会等待并继续轮询；`--max-steps` 限制推进次数，达到上限后查看返回状态再继续，不复用旧判断请求。

## 暂停、恢复和交付

`build pause <task_id> --project-root <PROJECT>` 暂停；`build resume <task_id> --mode agent --project-root <PROJECT>` 切换到宿主模式（自动模式用 `--mode auto`）并保留有效结果。`build reconfigure <task_id> --set KEY=VALUE --project-root <PROJECT>` 显式改变活动任务快照并使受影响阶段失效；已完成任务修改这类配置前先执行 `resume --from`，`read_limit` 等不触发构建的调整仍可直接更新；`build resume <task_id> --from chunking --project-root <PROJECT>` 显式重置切片及后续结果；`--from` 也可指定 `parsing`、`indexing` 或 `finalize`。活动任务的输入变更会重新解析。活动任务的随包判断规则变更会保留解析结果并重新从切片判断，旧请求不再有效；已有成品仍遵守覆盖冲突规则；用户明确授权覆盖后，使用 `build resume <task_id> --force --project-root <PROJECT>`，再继续推进；省略 `--force` 不撤销已授权的覆盖。任务私有状态在项目 `.learnova/builds/`，不应分发。

项目技能的管理命令以当前工作目录为项目根：在 `<PROJECT>` 目录运行 `list --project` 和 `uninstall <name> --project`；社区升级不会覆盖本地构建技能。用户明确要求打包刚生成的任务时，`build package <task_id> --project-root <PROJECT>` 校验当前成品并生成 ZIP；命令不上传或发布。

完成后的 status/next/普通 resume 仅返回构建历史；成品可自由编辑或卸载，原输入移动和规则升级也不触发重建。确需重新生成时对原任务执行 `build resume <task_id> --from finalize --project-root <PROJECT>` 再 next；保留解析、切片和元数据，现有目标仍须明确授权 `--force` 才替换。package 校验当前 SKILL.md 元数据、安全路径和当前本地参考/图片，打包新增及编辑内容，排除所有隐藏文件/目录、管理记录、私有状态、版本库和凭证文件，不比对生成快照。来源记录自动生成，用户无需填写。

`build read` 读取私有任务中的生成快照，只用于构建检查或继续生成。日常使用 Skill 时直接读取当前成品的 `SKILL.md` 和参考文件，用户编辑后的内容以成品目录为准。

交付时告知成品路径；要在宿主中使用，需让宿主加载该路径或明确读取其中的 `SKILL.md`，不要承诺自动实时重载。
