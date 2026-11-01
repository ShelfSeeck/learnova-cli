# 安装与本地管理

用于安装候选 Skill，以及查看、升级和卸载本地 Skill。

## 安装

```bash
python "<skill-dir>/scripts/learnova.py" install <skill>[@version] --json
```

默认安装最新版到用户级，并安装到所有检测到的 Agent skills 目录。

可选参数：

- `--project`：安装到当前工作目录的项目级 skills。
- `--agent <agent>`：限定目标 Agent，可重复指定。
- `--force`：覆盖已经存在的同名 Skill。只有用户明确要求覆盖时使用。

当前支持的 Agent：

| Agent | 用户级目录 | 项目级目录 |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| OpenCode | `~/.agents/skills/` | `.agents/skills/` |
| Codex CLI | `~/.codex/skills/` | `.agents/skills/` |

安装完成后，读取安装目标中的新 `SKILL.md`，再按该 Skill 的说明继续原任务。不要假设安装动作本身已经让当前 Agent 自动加载新说明。

## 查看已安装

```bash
python "<skill-dir>/scripts/learnova.py" list --json
```

项目级：

```bash
python "<skill-dir>/scripts/learnova.py" list --project --json
```

`install` 和 `list` 返回检测到的实际 Skill 目录路径；说明结果时同时报告来源、已记录的安装版本和路径。社区安装记录随 Skill 保存，编辑内容不会自动改变记录的版本；本地构建记录本地来源，结果提供输出路径，没有社区版本。`list` 扫描受管理的 Agent skills 目录重建状态，不依赖单独的已安装数据库，也不保证发现移到这些目录之外的文件。

## 升级

升级单个 Skill：

```bash
python "<skill-dir>/scripts/learnova.py" upgrade <skill> --json
```

升级全部：

```bash
python "<skill-dir>/scripts/learnova.py" upgrade --all --json
```

项目级操作加 `--project`。只有当前数据源存在更高版本号时才升级，社区来源的多个目标作为同一技能的一次事务更新。升级跳过 `local-build` 和 `unknown` 来源，避免覆盖本地作品或来源不明的目录。

## 卸载

```bash
python "<skill-dir>/scripts/learnova.py" uninstall <skill> --json
```

项目级操作加 `--project`。

需要先发现候选 Skill 时读取 [discovery.md](discovery.md)。准确参数总表见 [commands.md](commands.md)。
