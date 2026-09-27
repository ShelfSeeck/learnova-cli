---
name: learnova-cli
description: Learnova 技能社区的命令行入口。当用户想要搜索、安装、升级或卸载 Learnova 社区的 agent skill 时使用。支持 Claude Code、OpenCode、Codex CLI。
---

# Learnova CLI

Learnova 技能社区的分发工具。当前通过 Git 镜像提供检索与安装链路；账号、在线后端检索和发布功能尚未实现，不要调用相关命令。

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
| 修改配置 | `config set <键> <值>` |

## 行为约定

- **默认安装到用户级**（所有检测到的 agent 的 skills 目录）；`--project` 安装到当前工作目录的项目级 skills 目录；`--agent` 限定目标 agent。
- **同名冲突默认报错**，只有用户明确要求覆盖时才加 `--force`。
- 当前在未配置后端时使用镜像源；`search --offline` 只读取已有本地索引缓存，绝不联网。

## 需要用户亲自操作的场景

尚未实现的 `init` / `login` / `publish` / `status` 不要调用。

## 隐私说明

当前未启用实际遥测发送端点。后续接入后仍按规范仅在登录状态上报 install，并支持 `config set telemetry off` 或 `LEARNOVA_TELEMETRY=0` 关闭。
