# Mirror Sync 部署

`mirror/sync.py` 本身是幂等 one-shot CLI。这里的文件只负责调度，不包含镜像业务逻辑。

## 运行前准备

运行机器需要：

1. Python 3.10+ 和 `git`。
2. 能只读访问 Learnova SQLite 和 `archived_skill/`。
3. 一个持久的本地 Git 工作目录。
4. 能向正式 skills-mirror 仓库 push 的 Git/SSH 身份。

推荐三个目录彼此独立：

```text
<learnova>/data/project.db
<learnova>/archived_skill/
<mirror-workdir>/repo/
```

同步程序不会根据其中一个路径推导其他路径。

## 环境配置

复制 `env.example` 到服务器私有配置文件，例如：

```text
~/.config/learnova-mirror.env
```

至少填写：

```text
LEARNOVA_MIRROR_DATABASE
LEARNOVA_MIRROR_ARCHIVE_ROOT
LEARNOVA_MIRROR_REPO
```

Git 凭证不写入 env 文件。SSH Deploy Key、ssh-agent、credential helper 等由 Git 自己处理。

## 首次初始化工作目录

正式镜像仓应从空仓开始。创建本地工作目录并配置 remote：

```bash
mkdir -p <mirror-workdir>/repo
git -C <mirror-workdir>/repo init -b main
git -C <mirror-workdir>/repo remote add origin <skills-mirror-git-url>
git -C <mirror-workdir>/repo config user.name "Learnova Mirror"
git -C <mirror-workdir>/repo config user.email "learnova-mirror@localhost"
```

先执行：

```bash
python mirror/sync.py --dry-run
```

确认源数据可读后执行：

```bash
python mirror/sync.py --no-push
```

检查本地 Git 历史与 `index.json` 后，再执行不带这两个参数的正式同步。

## systemd

`systemd/` 是当前 Linux 生产环境推荐方式，但不是同步器依赖。

`learnova-skill-mirror.service.in` 需要替换：

- `@RUN_USER@`：执行同步的系统用户。
- `@PROJECT_DIR@`：learnova-cli 仓库绝对路径。
- `@ENV_FILE@`：私有 env 文件绝对路径。
- `@PYTHON@`：Python 解释器绝对路径。

渲染后安装为：

```text
/etc/systemd/system/learnova-skill-mirror.service
/etc/systemd/system/learnova-skill-mirror.timer
```

service 是 `Type=oneshot`，timer 默认每 10 分钟触发一次。同步器自身不常驻。

## cron

`cron.example` 展示了同一个 `mirror/sync.py` 如何由 cron 调用。Windows Task Scheduler、Kubernetes CronJob 或 CI 也应直接调用同一个入口，不复制同步逻辑。
