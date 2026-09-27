# 镜像运行与维护手册

本文面向 Learnova 的项目维护者和部署人员，说明第一次需要准备什么，以及镜像链路上线后如何维护。

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
GitLab 镜像仓
  一个仓库存放多个 Skill
        │
        │ HTTP 只读
        ▼
learnova-cli
  search / info / install / upgrade
```

维护时只需记住两点：

- Learnova 后端保存社区的正式数据。
- GitLab 镜像仓是自动生成的分发副本，不应手工修改 Skill 文件和 `index.json`。

## 2. 第一次配置顺序

### 2.1 创建 GitLab 镜像项目

创建一个镜像项目，例如：

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

基础设置：

| 项目 | 要求 |
|---|---|
| 默认分支 | `main` |
| Force push | 关闭 |
| Skill 布局 | 一个 Skill 一个目录 |
| 人工修改 Skill / index.json | 禁止 |
| 读取权限 | CLI 所在环境能够无需 GitLab 登录访问 raw 文件和 archive API |

当前 CLI 不保存 GitLab 用户凭证。因此镜像如果必须登录才能读取，CLI 将无法使用。现阶段应保证目标用户可以匿名读取镜像。

### 2.2 保护 main 分支

`main` 是正式镜像分支。

目标是：

- 普通成员不直接向 `main` 写镜像内容。
- 禁止 force push。
- 镜像同步程序可以正常 push。
- 维护者在同步程序故障时仍可以恢复仓库。

GitLab 的具体角色名称可能随实例配置不同。设置后要实际验证一次同步身份的 push 权限，不只看页面配置。

### 2.3 创建同步程序的 Git 凭证

同步程序需要执行：

```text
clone / fetch
commit
push
```

推荐选择一种：

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

实际目录、systemd unit 和环境变量名称等到实现同步程序时再确定，不提前形成无用约定。

## 3. 同步程序负责什么

同步程序的目标是让 GitLab 镜像等于当前需要公开分发的 APPROVED Skill 集合。

一次同步：

```text
读取 APPROVED Skill
        ↓
确定稳定的 skill-dir
        ↓
与镜像工作区对账
        ↓
新增 / 更新 / 删除已下架内容
        ↓
重新生成 index.json
        ↓
校验
        ↓
commit
        ↓
push main
```

一次同步中的所有变更使用一个 commit。

同步失败不能影响 Learnova 的审核结果。修复问题后重新执行同步即可。

### 3.1 实现同步程序前还要确定一个问题

`mirror-spec.md` 要求 Skill 发布后的镜像目录保持稳定，即使 Skill 后续改名也不能改变。

因此必须持久保存：

```text
community_id → skill-dir
```

不能每次根据当前 `name` 临时计算。

这个映射应由 Learnova 权威侧保存。建议在后端为社区 Skill 保存稳定的镜像目录字段，而不是让同步脚本自己维护另一份隐藏状态。

在正式编写镜像同步脚本前，需要先把这一点落实。

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

### GitLab

确认：

- `main` 已保护，force push 已关闭。
- 同步身份可以 push。
- CLI 所在环境无需 GitLab 登录即可读取镜像。
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
GitLab 产生同步 commit
```

然后：

1. 更新一次 Skill，确认新版本生成新的 commit。
2. 确认旧版本仍可通过旧 commit 定位。
3. 下架 Skill，确认工作区目录删除，但 Git 历史仍保留。

### CLI

使用真实镜像地址执行：

```bash
python scripts/learnova.py config set mirror_repo_url <mirror-url>
python scripts/learnova.py search <测试关键词>
python scripts/learnova.py info <skill-dir>
python scripts/learnova.py install <skill-dir>
python scripts/learnova.py list
```

全部成功后，镜像分发链路才算真正可用。

## 6. 日常维护

| 情况 | 处理方式 |
|---|---|
| 新 Skill / 新版本审核通过 | 等待同步，不手工改 GitLab |
| Skill 下架 | 由同步程序处理 |
| 同步失败 | 查看日志，修复原因后重新同步 |
| Git 凭证到期 | 更换服务器凭证并重新验证 push |
| `mirror-spec.md` 改动 | 先改规范，再改同步程序和 CLI |
| index schema 改动 | 提升 `schema_version`，同步修改 CLI |
| 镜像仓被误改 | 不继续手修，从 Learnova 数据重新生成 |

## 7. 账号与发布功能暂时不需要准备

开始接入账号、在线后端检索和发布功能时再处理：

- Learnova CLI 测试账号。
- 正式 `backend_url`。
- CLI 登录和 refresh 凭证方式。
- publish-status 接口。
- install telemetry 接口。
- Backend 与 Mirror 的在线回退。

这些不影响镜像同步与分发链路先独立完成。

## 8. 镜像同步上线前检查表

开始实现和部署同步程序前，确认：

- [ ] 已创建一个 GitLab monorepo 镜像项目。
- [ ] 默认分支为 `main`，force push 已关闭。
- [ ] 已配置 `main` 的写入权限。
- [ ] CLI 环境可以匿名读取镜像。
- [ ] 已准备独立的可写 Git 凭证。
- [ ] 凭证只保存在部署服务器。
- [ ] 已确定服务器工作目录和运行用户。
- [ ] 已确定 `community_id → skill-dir` 的持久化方式。
- [ ] 同步程序只投影 APPROVED Skill。
- [ ] 已准备首次端到端测试 Skill。

这些条件齐全后，再开始镜像同步程序的实际开发和部署。
