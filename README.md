# Learnova CLI

Learnova 技能社区的命令行入口，以 agent skill 形式分发。

支持从 Markdown 或 PDF 构建本地知识参考型 Skill；详见 [构建指南](skills/learnova-cli/references/building.md)。PDF 与自动模式需要用户配置解析服务和模型。

可检索、查看、安装、升级和卸载社区技能。未登录时使用 Git 镜像；配置后端并登录后优先在线检索，后端不可用时按错误类型回退镜像。也支持初始化、账号登录、提交发布和查询审核状态。

## 安装

正式维护与分发仓库为 [learnova-cli](https://git.nju.edu.cn/learnova/learnova-cli)。Skill 放在 `skills/learnova-cli/`，推荐安装到用户级：

```bash
npx skills add https://git.nju.edu.cn/learnova/learnova-cli.git \
  --skill learnova-cli \
  --global
```

如只希望当前项目使用，去掉 `--global`。

安装后 agent 会按 Skill 内的 `SKILL.md` 指引调用脚本。开发仓中也可直接运行：

```bash
python skills/learnova-cli/scripts/learnova.py --help
```

## 快速上手

```bash
python skills/learnova-cli/scripts/learnova.py search 代码审查
python skills/learnova-cli/scripts/learnova.py install <技能名>
```

CLI 默认使用正式镜像 `https://git.nju.edu.cn/learnova/skills-mirror`，无需首次配置。需要切换到其他镜像时仍可执行：

```bash
python skills/learnova-cli/scripts/learnova.py config set mirror_repo_url <镜像仓地址>
```

完整命令表、参数与默认行为见 [docs/cli-spec.md](docs/cli-spec.md)。

需要账号或发布时，先配置部署提供的 API 根地址，再由本人在终端登录：

```bash
python skills/learnova-cli/scripts/learnova.py config set backend_url https://api.example.invalid
python skills/learnova-cli/scripts/learnova.py login
python skills/learnova-cli/scripts/learnova.py whoami
python skills/learnova-cli/scripts/learnova.py publish ./sample-skill --version 1.0.0 --changelog "Initial release"
python skills/learnova-cli/scripts/learnova.py status
```

`login` 不接受密码参数或密码环境变量，Agent 不代输凭证。发布成功表示已提交审核；审核上架并同步镜像后才可通过镜像安装。发布排除隐藏文件、私有状态和凭证文件；服务端仍可能限制 `scripts/` 等资源目录，不应绕过。详见 [账号与发布](skills/learnova-cli/references/accounts.md)。

## 隐私

未登录零上报。后端部署并启用专用事件端点后，仅登录且显式配置同源 `telemetry_endpoint` 时，成功安装才可能发送技能目录名、版本和时间戳；未配置端点不发送。可用 `config set telemetry off` 或环境变量 `LEARNOVA_TELEMETRY=0` 关闭。发送失败或缺少有效访问凭证会丢弃事件，不影响安装，也不在后台刷新登录态。详见 [docs/cli-spec.md](docs/cli-spec.md) §6。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/cli-spec.md](docs/cli-spec.md) | 命令参考、配置项、本地存储、agent 适配、检索与遥测 |
| [docs/mirror-spec.md](docs/mirror-spec.md) | git 镜像仓规范（目录布局、index.json schema、同步约定） |
| [docs/operations.md](docs/operations.md) | 运行与维护手册（镜像仓配置、同步凭证、服务器准备、镜像链路验收） |
| [docs/architecture.md](docs/architecture.md) | 实现架构（贡献者向） |
| [docs/maintenance.md](docs/maintenance.md) | 维护手册：如何加新功能 |
| [docs/ci.md](docs/ci.md) | 本地验收、可选真实服务检查与平台运行条件 |

## 运行要求

Python 3.10+；基础功能无需第三方依赖。PDF 拆页和本地 Gradio 解析按需安装可选依赖，见构建指南。
