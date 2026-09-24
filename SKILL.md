---
name: learnova-cli
description: Learnova 技能社区的命令行入口。当用户想要搜索、安装、升级或发布 Learnova 社区的 agent skill 时使用。支持 Claude Code、OpenCode、Codex CLI。
---

# Learnova CLI

Learnova 技能社区的分发工具。社区仓库镜像托管在校内 GitLab，权威数据（检索、账号、审核状态）由 Learnova 后端提供。

## 使用方式

所有功能通过 Python 脚本调用，一律使用 `--json` 获取结构化输出：

```bash
python "{SKILL_DIR}/scripts/learnova.py" <命令> [参数] --json
```

Windows PowerShell 环境使用薄入口：

```powershell
& "{SKILL_DIR}/learnova-cli.ps1" <命令> [参数] --json
```

## 命令一览

| 场景 | 命令 |
|---|---|
| 搜索技能 | `search <关键词> [--tag 标签] [--sort popular\|newest\|likes]` |
| 查看详情 | `info <技能名>`（含版本列表与 README） |
| 安装技能 | `install <技能名>[@版本] [--project] [--agent claude] [--force]` |
| 查看已安装 | `list [--project]` |
| 卸载 | `uninstall <技能名> [--project]` |
| 升级 | `upgrade [<技能名>\|--all]` |
| 查看发布审核状态 | `status [<技能名>]` |
| 发布技能 | `publish <目录> --version x.y.z --changelog "说明"` |
| 初始化配置 | `init` |
| 修改配置 | `config set <键> <值>` |

## 行为约定

- **默认安装到用户级**（所有检测到的 agent 的 skills 目录）；`--project` 安装到当前工作目录的项目级 skills 目录；`--agent` 限定目标 agent。
- **同名冲突默认报错**，只有用户明确要求覆盖时才加 `--force`。
- 检索默认走后端 API；后端不可达时自动降级为本地缓存的社区索引，输出会标注数据可能陈旧。

## 需要用户亲自操作的场景

以下命令涉及凭证或交互确认，**不要代替用户执行**，请提示用户在自己的终端运行：

- `login`：登录 Learnova 账号（需要输入用户名和密码）
- `init`：首次初始化配置
- `publish` 前的版本号和 changelog 应与用户确认后再执行

## 隐私说明

仅在用户已登录账号时，`install` 会向社区上报一次安装事件（技能名、版本、时间），用于统计技能活跃度。用户可通过 `config set telemetry off` 或环境变量 `LEARNOVA_TELEMETRY=0` 关闭。未登录时不产生任何上报。
