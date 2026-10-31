# 命令参考

所有命令通过同一个 Python 入口调用，并附加 `--json`：

```bash
python "<skill-dir>/scripts/learnova.py" <command> [args] --json
```

| 场景 | 命令 |
|---|---|
| 初始化 | `init` |
| 搜索 | `search <keyword> [--tag TAG] [--sort popular\|newest\|likes] [--offline]` |
| 详情 | `info <skill>` |
| 安装 | `install <skill>[@version] [--project] [--agent AGENT] [--force]` |
| 已安装列表 | `list [--project]` |
| 卸载 | `uninstall <skill> [--project]` |
| 升级单个 | `upgrade <skill> [--project]` |
| 升级全部 | `upgrade --all [--project]` |
| 查看配置 | `config` 或 `config get` |
| 修改配置 | `config set <key> <value>` |
| 本人终端登录 | `login` |
| 查看当前账号 | `whoami` |
| 退出并清除本地凭证 | `logout` |
| 提交发布 | `publish <directory> --version x.y.z --changelog "..."` |
| 查询本人审核状态 | `status [<skill-name-or-id>]` |

`--json` 可以放在子命令前后任意位置。

账号及发布需要配置 `backend_url`。`login` 必须由本人在真实终端运行；`whoami` 有凭证时验证线上账号，`logout` 尝试吊销并清除本地凭证。发布成功表示提交审核。细节见 [accounts.md](accounts.md)。

如果需要理解命令应该在什么场景使用，优先读取相应 workflow 文档，不要只靠本页参数表推断行为。

构建命令包括 `build create|next|read|submit|run|status|pause|resume|reconfigure|package|config`；参数、结果和完整流程见 [building.md](building.md)。
