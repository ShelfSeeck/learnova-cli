# 命令参考

所有命令通过同一个 Python 入口调用，并附加 `--json`：

```bash
python "<skill-dir>/scripts/learnova.py" <command> [args] --json
```

| 场景 | 命令 |
|---|---|
| 搜索 | `search <keyword> [--tag TAG] [--sort popular\|newest\|likes] [--offline]` |
| 详情 | `info <skill>` |
| 安装 | `install <skill>[@version] [--project] [--agent AGENT] [--force]` |
| 已安装列表 | `list [--project]` |
| 卸载 | `uninstall <skill> [--project]` |
| 升级单个 | `upgrade <skill> [--project]` |
| 升级全部 | `upgrade --all [--project]` |
| 查看配置 | `config` 或 `config get` |
| 修改配置 | `config set <key> <value>` |
| 查看本地登录状态 | `whoami` |
| 清除本地凭证 | `logout` |

`--json` 可以放在子命令前后任意位置。

当前不要调用 `init`、`login`、`publish`、`status`；这些命令仍为空实现。`whoami` 仅查看本地凭证状态，不验证线上有效性；`logout` 仅清除本地凭证。

如果需要理解命令应该在什么场景使用，优先读取相应 workflow 文档，不要只靠本页参数表推断行为。

常规构建使用 `build start <input>`、`build work <task-id>`、`build submit <task-id> --file <result.json> --continue`；`build doctor <input>` 只读诊断，`build config preset mineru` 一次配置云解析。模型 API 可选，启用时给 start 加 `--mode auto`。旧 `create|next|read|submit|run|status|pause|resume|reconfigure|package|config` 保留；参数、结果和完整流程见 [building.md](building.md)。
