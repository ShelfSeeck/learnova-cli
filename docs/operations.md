# 镜像运行与维护手册

本文面向 Learnova 的项目维护者和部署人员，说明第一次需要准备什么，以及镜像链路上线后如何维护。

正式镜像及同步服务已部署。首次配置步骤用于新环境或重建；已有环境核查当前配置与运行记录，不重复部署。

数据格式与代码行为分别以 [mirror-spec.md](mirror-spec.md)、[cli-spec.md](cli-spec.md) 和 [architecture.md](architecture.md) 为准。本文只讲实际配置和维护步骤。

## 1. 最小模型

当前镜像同步与 CLI 分发链路如下：

```text
Learnova 后端
  保存社区 Skill 与审核结果
        │
        │ APPROVED
        ▼
镜像同步程序
  对账、生成目录和 index.json
        │
        │ git push
        ▼
远程 Git 镜像仓
  一个仓库存放多个 Skill
        │
        │ HTTP 只读
        ▼
learnova-cli
  search / info / install / upgrade
```

维护边界：

- Learnova 后端保存社区的正式数据。
- 远程 Git 镜像仓是自动生成的分发副本，不应手工修改 Skill 文件和 `index.json`。
- 镜像同步属于 learnova-cli 的分发基础设施。Learnova 当前只提供数据库与归档文件作为只读输入，不需要为镜像模型增加代码、字段或服务。

## 2. 第一次配置顺序

### 2.1 创建远程 Git 镜像仓

在可长期访问的 Git 托管服务上创建一个镜像仓，例如：

```text
<group>/skills-mirror
```

使用单仓 monorepo：

```text
skills-mirror/
├── index.json
├── SPEC.md
├── skill-a/
├── skill-b/
└── skill-c/
```

不要为每个 Skill 单独创建仓库。

第一次创建时建议使用**空仓库**：不要预先生成 README、LICENSE、`.gitignore` 等文件。
首个有效 commit 应由镜像初始化/同步流程产生，而不是人工在网页端补文件。

如果当时还没有任何 APPROVED Skill，首个 commit 至少可以包含：

```text
SPEC.md
index.json
```

其中 `index.json` 是合法的空索引。后续 Skill 目录仍全部由同步程序生成。

基础设置：

| 项目 | 要求 |
|---|---|
| 默认分支 | `main` |
| Force push | 关闭 |
| Skill 布局 | 一个 Skill 一个目录 |
| 人工修改 Skill / index.json | 禁止 |
| 读取权限 | CLI 所在环境能够无需额外登录读取镜像所需的 raw 文件和历史版本内容 |

当前 CLI 不保存镜像仓读取凭证。因此镜像如果必须登录才能读取，CLI 将无法使用。现阶段应保证目标用户可以匿名读取镜像。

仓库本身不要求必须托管在 GitLab。需要注意的是，**当前 CLI 获取历史版本的实现使用 GitLab-compatible archive API**。如果以后换用不兼容该 API 的 Git 托管服务，需要同时增加对应的历史版本下载适配。

### 2.2 保护 main 分支

`main` 是正式镜像分支。

目标是：

- 普通成员不直接向 `main` 写镜像内容。
- 禁止 force push。
- 镜像同步程序可以正常 push。
- 维护者在同步程序故障时仍可以恢复仓库。

不同 Git 托管服务的分支保护和角色名称不同。设置后要实际验证一次同步身份的 push 权限，不只看页面配置。

### 2.3 创建同步程序的 Git 凭证

同步程序需要执行：

```text
clone / fetch
commit
push
```

凭证形式取决于所用 Git 托管服务。对于 GitLab-compatible 服务，推荐选择一种：

1. **Project Access Token**：限定在镜像项目，授予 `write_repository`。
2. **可写 Deploy Key**：使用 SSH，并在分支规则中允许该 Key 写入 `main`。

不要使用 GitLab Deploy Token 做 Git push。Deploy Token 的 repository 权限用于只读仓库访问。

凭证只保存在部署服务器，不进入任何 Git 仓库，也不要写进日志和示例配置。

### 2.4 准备服务器工作目录

同步程序需要一个持久目录保存镜像仓的本地 clone：

```text
<mirror-workdir>/
└── repo/
```

要求：

- 由运行同步程序的系统用户持有。
- 凭证通过服务器安全配置提供，不写入工作目录。
- 该目录损坏后可以直接重新 clone。

实际工作目录和环境变量名称在部署时确定；systemd unit 的推荐名称见后文，仓库文档不写死服务器绝对路径。

## 3. 同步程序负责什么

同步程序的目标是让远程 Git 镜像等于当前需要公开分发的 APPROVED Skill 集合。

Learnova 服务器已经保存发布后的版本文件。同步程序应直接读取 Learnova 的权威数据库和对应的社区归档目录，不需要通过公开 HTTP API 再把 Skill 下载回来。

同步程序属于 learnova-cli 仓库，但可以部署到任何同时满足以下条件的机器：

- 能读取 Learnova 数据库。
- 能读取 Learnova 的社区归档目录。
- 能读写一个持久的镜像 Git 工作目录。
- 能访问远程 Git 仓并完成 push。

它不 import Learnova 后端代码，也不依赖 Learnova 的 Python 虚拟环境。建议在 learnova-cli 中保持独立目录：

```text
learnova-cli/
├── skills/
│   └── learnova-cli/          # npx skills 实际分发的用户侧运行时
└── mirror/
    ├── sync.py                # one-shot 同步入口
    ├── source.py              # 读取数据库与 archived_skill
    ├── repository.py          # Git 工作树与 commit/push
    ├── index.py               # 生成和校验 index.json
    └── deploy/                # 可选调度器模板，不属于核心运行逻辑
```

服务器另外准备一个独立工作目录：

```text
<mirror-workdir>/
└── repo/     # 远程镜像仓的 clone
```

这个目录只是可重建的 Git 工作副本，不属于 Learnova 的权威业务数据。

### 3.1 同步程序的运行边界

同步程序本身是一个**短生命周期、幂等的 one-shot CLI**：

```text
启动
→ 获取互斥锁
→ 读取源数据
→ 对账与生成 commit
→ 可选 push
→ 释放锁
→ 退出
```

它不包含 `while true + sleep` 这类常驻循环，也不自己负责开机启动、重启策略或运行频率。这些职责交给外部调度器，因此同一个程序可以由 systemd timer、cron、Windows Task Scheduler、Kubernetes CronJob、CI 或人工命令调用。

核心实现遵循以下约束：

- Python 3.10+；尽量只使用标准库，与用户侧 CLI 保持一致。
- 外部只要求存在 `git` 可执行文件。
- 不调用 systemd、cron 或其他特定平台 API。
- 不写死服务器用户、绝对路径、数据库位置或归档位置。
- Git/SSH 凭证由运行环境提供；同步程序不接收私钥内容，也不把 token 写入工作目录。
- 日志只写 stdout/stderr，由调用它的运行环境负责采集。
- 重复执行必须安全；没有变化时不产生 commit，也不 push。
- 同一个镜像工作目录同一时间只允许一个同步进程修改。

为了兼容不同系统，互斥实现不依赖 Linux 专有的 `flock`。实现时使用跨平台的原子 lock 文件/目录，并保存足够的信息用于识别异常退出留下的过期锁。

### 3.2 配置接口

服务器差异全部通过参数或环境变量输入。预期核心参数如下：

| 参数 | 环境变量 | 含义 |
|---|---|---|
| `--database` | `LEARNOVA_MIRROR_DATABASE` | Learnova SQLite 数据库路径 |
| `--archive-root` | `LEARNOVA_MIRROR_ARCHIVE_ROOT` | `archived_skill` 根目录 |
| `--repo` | `LEARNOVA_MIRROR_REPO` | 本地镜像 Git 工作目录 |
| `--remote` | `LEARNOVA_MIRROR_REMOTE` | push remote，默认 `origin` |
| `--branch` | `LEARNOVA_MIRROR_BRANCH` | 正式分支，默认 `main` |
| `--dry-run` | — | 完成读取、对账与校验，但不修改 Git 工作树 |
| `--no-push` | — | 允许生成本地 commit，但不 push |

同一配置同时出现时，命令行参数优先于环境变量。凭证不属于这套配置接口，应直接使用 Git 原生能力，例如 SSH config、ssh-agent、Deploy Key、credential helper 或 CI secret 注入。

手工执行形态例如：

```bash
python mirror/sync.py \
  --database /path/to/project.db \
  --archive-root /path/to/archived_skill \
  --repo /path/to/mirror-workdir/repo
```

路径只是示例；同步程序不要求这些目录位于同一项目树或同一固定前缀。

### 3.3 一轮同步怎么执行

一轮同步可以只 push 一次，但会根据新版本数量产生多个 commit：

```text
读取 APPROVED 版本
        ↓
从 index.json 和 Git 历史判断哪些版本已经镜像
        ↓
按稳定顺序处理每个新版本
        ↓
复制 archived_skill 中对应版本
→ 更新 <skill-dir>/
→ 创建内容 commit
→ 保存 commit hash
        ↓
删除当前已不应公开的 Skill 目录
→ 如有变化创建状态 commit
        ↓
根据数据库和已确定的 commit hash
全量重新生成 index.json
        ↓
创建 index commit
        ↓
push main
```

例如同一轮发现：

```text
foo 1.1 APPROVED
foo 1.2 APPROVED
bar 2.0 APPROVED
```

Git 历史会形成：

```text
commit A   foo 1.1
commit B   foo 1.2
commit C   bar 2.0
commit D   update index.json
```

`index.json` 中保存：

```text
foo 1.1 → A
foo 1.2 → B
bar 2.0 → C
```

最后只需要把 A～D 一次性 push 到远程仓。

同步失败不能影响 Learnova 的审核结果。修复问题后重新执行同步即可。

### 3.4 中断后怎么恢复

同步脚本必须是幂等的。

如果它在内容 commit 已经创建、但 `index.json` 还没更新时中断，下次运行需要从 Git commit message 中恢复：

```text
community_id + version
→ 已存在的内容 commit hash
```

因此内容 commit 会包含稳定的 Learnova 标识。恢复后继续生成 index，而不是为同一版本再创建一次 commit。

如果远程 push 失败，本地 commit 可以保留在 mirror 工作目录中；下一轮先与远程 `main` 对齐，再继续同步和重试 push。

### 3.5 稳定的 skill-dir 由镜像层维护

`mirror-spec.md` 要求 Skill 发布后的镜像目录保持稳定，即使 Skill 后续改名也不能改变。

因此必须持久保存：

```text
community_id → skill-dir
```

不能每次根据当前 `name` 临时计算。

Learnova 当前不为镜像增加字段。该映射由镜像层自身恢复：

```text
当前仍在镜像中的 Skill
→ 从 index.json 读取 community_id → dir

曾经下架、后来重新上架的 Skill
→ 从 Git 历史中的内容 commit trailer / 历史 index 恢复原 dir

第一次进入镜像的 community_id
→ 根据 name 分配 dir
→ 冲突时追加 --<id前4位>
```

因此不额外维护另一份隐藏状态文件，也不要求修改 Learnova 数据库。Git 镜像自身包含恢复该映射所需的历史。

### 3.6 调度器只是外层适配

Linux 服务器上推荐 systemd，因为它普遍、日志和失败状态清楚，但 systemd 不是同步程序的运行前提。推荐包装为：

```text
learnova-skill-mirror.service   # oneshot：执行一轮同步
learnova-skill-mirror.timer     # 定期触发 service
```

service 的职责只应是设置工作目录/环境并执行：

```text
python mirror/sync.py
```

其他环境直接使用同一个入口：

```text
cron                   → python mirror/sync.py
Windows Task Scheduler → python mirror/sync.py
Kubernetes CronJob     → python mirror/sync.py
CI                     → python mirror/sync.py
人工维护               → python mirror/sync.py --dry-run / --no-push
```

这些适配层不能复制同步业务逻辑。

第一版在当前 Linux 生产环境优先提供 systemd oneshot + timer 模板。Learnova 审核接口只负责修改自身业务状态，不直接执行 Git push；同步程序读取最终 APPROVED 状态并自行对账，因此镜像故障不会阻塞审核。

后续如果需要更快同步，可以在审核通过后触发同一个 oneshot service；不要另写第二套同步逻辑。

仓库内的可直接使用模板见 `mirror/deploy/`。其中 `env.example` 定义部署配置，`systemd/` 和 `cron.example` 只负责调度。

## 4. 镜像仓内容

工作区只保留每个 Skill 的最新 APPROVED 版本：

```text
skill-a/
├── SKILL.md
├── README.md       # 可选
├── references/     # 可选
└── assets/         # 可选
```

历史版本保存在 Git 历史中，不建立 `1.0.0/`、`1.1.0/` 这类版本目录。

仓根：

- `index.json`：同步程序全量生成。
- `SPEC.md`：`mirror-spec.md` 的镜像仓副本或等价说明。

这两个文件同样不应手工维护。

## 5. 第一次上线验收

镜像同步链路完成后，至少人工跑一次真实链路。

### 远程 Git 仓库

确认：

- `main` 已保护，force push 已关闭。
- 同步身份可以 push。
- CLI 所在环境无需额外登录即可读取镜像。
- 仓库里没有 token、密码或私钥。

### 同步链路

准备一个测试 Skill：

```text
Learnova 审核通过
        ↓
同步执行
        ↓
Skill 目录出现
        ↓
index.json 出现 community_id / dir / version / commit
        ↓
远程仓库产生同步 commit
```

然后：

1. 更新一次 Skill，确认新版本生成新的 commit。
2. 确认旧版本仍可通过旧 commit 定位。
3. 下架 Skill，确认工作区目录删除，但 Git 历史仍保留。

### CLI

CLI 默认使用正式镜像，可直接执行：

```bash
python skills/learnova-cli/scripts/learnova.py search <测试关键词>
python skills/learnova-cli/scripts/learnova.py info <skill-dir>
python skills/learnova-cli/scripts/learnova.py install <skill-dir>
python skills/learnova-cli/scripts/learnova.py list
```

如需测试其他镜像，再显式执行 `config set mirror_repo_url <mirror-url>` 覆盖默认值。

全部成功后，镜像分发链路才算真正可用。

## 6. 日常维护

| 情况 | 处理方式 |
|---|---|
| 新 Skill / 新版本审核通过 | 等待同步，不手工改镜像仓 |
| Skill 下架 | 由同步程序处理 |
| 同步失败 | 查看日志，修复原因后重新同步 |
| Git 凭证到期 | 更换服务器凭证并重新验证 push |
| `mirror-spec.md` 改动 | 先改规范，再改同步程序和 CLI |
| index schema 改动 | 提升 `schema_version`，同步修改 CLI |
| 镜像仓被误改 | 不继续手修，从 Learnova 数据重新生成 |

## 7. 账号与发布维护

CLI 已实现账号、在线检索、发布与审核查询。维护时核查对应配置与服务契约：

- `backend_url` 由部署配置；认证凭证绑定来源，修改地址后由本人重新登录。
- 登录、refresh Cookie 轮换与写请求防重放由账号服务处理；排查时不输出凭证或原始响应。
- 发布与审核查询复用现有接口；核对提交状态、本人可见范围与审核备注，区分待审和上架。
- Backend 与 Mirror 仅对可恢复网络、服务及认证错误回退；响应结构、技能包或明确不存在错误须保留。
- 安装计数依赖单独部署并启用的事件端点，客户端 `telemetry_endpoint` 默认留空。不得为计数调用会修改用户服务端技能库的安装接口。

账号与事件端点的部署不改变镜像同步程序的只读数据边界。事件客户端已实现，生产端部署和启用需单独核实。

## 8. 镜像同步环境核查表

以下项目用于新环境部署和现有环境复查，不是未完成的开发任务。正式镜像可匿名读取、同步服务已部署；运行日志和审核后自动更新记录按环境核查。

- 首次初始化使用空 monorepo 镜像仓。
- 默认分支为 `main`，关闭 force push，配置独立写入权限。
- CLI 环境可以匿名读取镜像。
- 写凭证由部署环境管理，不进入源码或日志。
- 工作目录和运行用户明确，能读取数据库与 `archived_skill`。
- `community_id → skill-dir` 可从镜像当前状态或 Git 历史恢复。
- 调度方式与环境一致；现有服务检查其运行记录。
- 同步程序只投影 APPROVED Skill。
- 验证场景包含审核后更新、重复同步和失败恢复。

新环境在配置齐全后部署同步程序；现有环境按需复查自动更新与失败恢复，不重新初始化镜像仓。
