# 账号与发布

镜像检索和安装无需登录。账号、在线源与发布需要部署提供的 `backend_url`；示例地址仅为占位符：

```bash
python "<skill-dir>/scripts/learnova.py" config set backend_url https://api.example.invalid --json
```

## 初始化和登录

`init --json` 检测 Agent 并展示配置。真实终端确认后保存；非交互环境返回 `status=needs_input`、`kind=configuration` 和问题，不等待输入。已有有效配置可重复使用，不覆盖显式配置。

请本人在真实终端运行以下命令并输入邮箱和密码，Agent 不代输、索取或保存密码：

```bash
python "<skill-dir>/scripts/learnova.py" login
```

登录不接受密码参数或密码环境变量。非终端调用返回 `BAD_USAGE`。不要把密码、Cookie、token 或 `auth.json` 内容放入对话、命令参数或文件。

```bash
python "<skill-dir>/scripts/learnova.py" whoami --json
python "<skill-dir>/scripts/learnova.py" logout --json
```

`whoami` 无凭证时返回未登录，有凭证时查询当前账号。刷新凭证保存在权限 600 的 `~/.learnova/auth.json`，绑定后端来源；切换后端后须重新登录，旧凭证不会发往新来源。`logout` 尝试吊销并清除本地凭证，后端不可达也可完成本地退出。

## 提交审核

发布普通 Skill 目录，不要求先创建文档构建任务。先检查当前文件，确认用户要公开的目录、版本号和变更说明，再调用：

```bash
python "<skill-dir>/scripts/learnova.py" publish ./sample-skill \
  --version 1.0.0 --changelog "Initial release" --json
```

显式参数即为本次确认。CLI 校验目录、生成 ZIP、上传为新条目并提交审核；保留当前 `SKILL.md` 和已编辑内容，不自动复用远端同名条目。成功结果的 `submitted=true` 和待审核状态只代表提交成功。上传后若提交失败，账号中可能保留草稿；先查看结果，不盲目重试上传。

发布和 `build package` 排除所有隐藏文件/目录、管理记录与已知凭证文件；本地链接若指向被排除文件须先整理为可公开资源。符号链接、特殊文件、不安全路径和缺失引用会被拒绝。后端仍会校验完整 YAML 和资源类型；部分部署限制 `scripts/` 等目录，客户端不丢弃内容或绕过限制。

```bash
python "<skill-dir>/scripts/learnova.py" status --json
python "<skill-dir>/scripts/learnova.py" status <skill-name-or-id> --json
```

`status` 仅查询当前账号的提交，包含版本状态与当前审核备注。名称有歧义时使用返回的社区或来源条目 ID。通过审核、上架并同步镜像后，其他用户才能从镜像检索安装。

本地打包使用 `build package`，不会上传；详见 [building.md](building.md)。遥测开关和数据范围见 [configuration.md](configuration.md)。
