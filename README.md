# Learnova CLI

Learnova 技能社区的命令行入口，以 agent skill 形式分发。让你的本地 agent 直接搜索、安装、发布社区技能。
主要功能是链接另外一个Learnova镜像

## 安装

```bash
npx skills add <owner>/learnova-cli@learnova-cli
```

安装后 agent 会按 SKILL.md 指引自动使用；也可手动运行：

```bash
python scripts/learnova.py --help
```

## 快速上手

```bash
python scripts/learnova.py init              # 首次初始化
python scripts/learnova.py search 代码审查    # 搜索社区技能
python scripts/learnova.py install <技能名>   # 安装到所有检测到的 agent
```

完整命令表、参数与默认行为见 [docs/cli-spec.md](docs/cli-spec.md)。

## 隐私

未登录时零上报。登录后安装技能时上报一次安装事件用于社区活跃度统计，可用 `config set telemetry off` 或环境变量 `LEARNOVA_TELEMETRY=0` 关闭。详见 [docs/cli-spec.md](docs/cli-spec.md) §6。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/cli-spec.md](docs/cli-spec.md) | 命令参考、配置项、本地存储、agent 适配、检索与遥测 |
| [docs/mirror-spec.md](docs/mirror-spec.md) | git 镜像仓规范（目录布局、index.json schema、同步约定） |
| [docs/architecture.md](docs/architecture.md) | 实现架构（贡献者向） |
| [docs/maintenance.md](docs/maintenance.md) | 维护手册：如何加新功能 |

## 运行要求

Python 3.10+，无第三方依赖。
