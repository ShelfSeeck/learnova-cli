# Learnova CLI

Learnova 技能社区的命令行入口，以 agent skill 形式分发。

当前实现聚焦镜像分发：从 Learnova 的 Git 镜像检索、查看、安装、升级和卸载社区技能。账号、在线后端检索与发布链路当前尚未实现。

## 安装

`learnova-cli` 作为独立 Skill 放在 `skills/learnova-cli/`，推荐安装到用户级：

```bash
npx skills add https://git.nju.edu.cn/learnova/learnova-cli.git \
  --skill learnova-cli \
  --global
```

如只希望当前项目使用，去掉 `--global`。

仓库也保留 GitHub remote，可使用：

```bash
npx skills add ShelfSeeck/learnova-cli --skill learnova-cli --global
```

安装后 agent 会按 Skill 内的 `SKILL.md` 指引调用脚本。开发仓中也可直接运行：

```bash
python skills/learnova-cli/scripts/learnova.py --help
```

## 快速上手

```bash
python skills/learnova-cli/scripts/learnova.py config set mirror_repo_url <镜像仓地址>
python skills/learnova-cli/scripts/learnova.py search 代码审查
python skills/learnova-cli/scripts/learnova.py install <技能名>
```

完整命令表、参数与默认行为见 [docs/cli-spec.md](docs/cli-spec.md)。

## 隐私

当前没有启用实际遥测发送端点。后续接入账号与遥测后，仍遵循“未登录零上报、可显式关闭”的规范。详见 [docs/cli-spec.md](docs/cli-spec.md) §6。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/cli-spec.md](docs/cli-spec.md) | 命令参考、配置项、本地存储、agent 适配、检索与遥测 |
| [docs/mirror-spec.md](docs/mirror-spec.md) | git 镜像仓规范（目录布局、index.json schema、同步约定） |
| [docs/operations.md](docs/operations.md) | 运行与维护手册（镜像仓配置、同步凭证、服务器准备、镜像链路验收） |
| [docs/architecture.md](docs/architecture.md) | 实现架构（贡献者向） |
| [docs/maintenance.md](docs/maintenance.md) | 维护手册：如何加新功能 |

## 运行要求

Python 3.10+，无第三方依赖。
