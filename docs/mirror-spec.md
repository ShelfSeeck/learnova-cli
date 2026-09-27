# 镜像仓规范

Learnova 技能社区的 git 镜像，托管于可长期访问的远程 Git 仓库。承担三个职责：社区内容的备份、第三方 agent 的分发源、基于 git 历史的版本管理。

本文档是该仓库结构与生成行为的唯一规范。仓内所有内容**由同步脚本生成，禁止手工修改**。

## 1. 仓库布局

单仓 monorepo。工作区（working tree）只放每个技能的**最新 APPROVED 版本**。

```
<repo-root>/
├── index.json              # 社区索引（规范见 §3）
├── SPEC.md                 # 本规范的副本或链接
└── <skill-dir>/            # 一个技能一个目录
    ├── SKILL.md
    ├── README.md
    ├── references/         # 可选
    └── assets/             # 可选
```

- 工作区布局与 `npx skills add <owner>/<repo>@<skill-dir>` 的约定兼容，拿到即是最新上架版。
- 历史版本不在工作区保留副本，全部通过 git 提交历史追溯（见 §4）。

## 2. 目录命名规则

- 目录名默认取技能的 `name`。
- 社区允许同名技能共存（按 UUID 区分），目录名冲突时追加短后缀：`<name>--<id前4位>`，如 `code-reviewer--a1b2`。
- **目录名是镜像层的唯一标识**。CLI 安装、index.json 引用均使用目录名；`name` 仅为展示名。
- 目录名一旦发布**不再变更**，即使技能改名（改名只体现在 index.json 的展示字段）。

## 3. index.json schema

仓根唯一索引文件，由同步脚本从社区数据库导出，是 DB 的投影而非第二份数据。

```json
{
  "schema_version": 1,
  "generated_at": 1727193600.0,
  "skills": [
    {
      "dir": "code-reviewer",
      "community_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "name": "code-reviewer",
      "display_name": "代码审查助手",
      "description": "一句话描述",
      "tags": ["code", "review"],
      "likes": 42,
      "downloads": 128,
      "latest_version": "1.2.0",
      "versions": [
        {
          "version": "1.2.0",
          "changelog": "新增 xxx",
          "commit": "a1b2c3d4",
          "size_bytes": 12345,
          "created_at": 1727193600.0
        }
      ]
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `schema_version` | schema 版本，破坏性变更时递增 |
| `generated_at` | 索引生成时间戳，CLI 据此提示数据陈旧 |
| `dir` | 仓内目录名，唯一标识 |
| `community_id` | 社区侧技能 UUID，用于与后端 API 数据对照 |
| `tags` / `likes` / `downloads` | 快照值：tags 取自最新 APPROVED 版本，计数取自生成时刻 |
| `versions[].commit` | 该版本内容对应的 commit hash，拉取旧版本的依据 |

`versions` 按时间升序，只含 APPROVED 版本。

## 4. 版本管理

- 版本历史 = git 提交历史。每个进入镜像的 APPROVED 版本都必须对应一个**内容 commit**，该 commit 中对应 `<skill-dir>/` 必须正好是这个版本的完整内容。
- `versions[].commit` 记录内容 commit 的 hash，不记录后续的索引 commit。
- 安装指定旧版本：按 `versions[].commit` 获取该 commit 的仓库归档并取对应技能目录。当前 CLI 使用 GitLab-compatible archive API 实现；其他托管服务需提供等价适配。
- 同一版本号内容不会变化（由社区审核流保证），升级判断只比较版本号。
- 工作区仍只保留最新 APPROVED 版本；旧版本只存在于 Git 历史中，不建立版本目录。

### 4.1 为什么 index.json 单独提交

`index.json` 需要记录版本对应的 commit hash，而 commit hash 只有在内容 commit 创建后才确定。因此 Skill 内容和引用它的索引不能依赖同一个 commit。

同步顺序必须是：

```text
写入 skill-a@1.1
→ content commit A
→ 得到 hash A

写入 skill-a@1.2
→ content commit B
→ 得到 hash B

根据已确定的 A / B 生成 index.json
→ index commit C
```

此时：

```text
skill-a@1.1 → commit A
skill-a@1.2 → commit B
HEAD         → commit C
```

### 4.2 内容 commit 的可恢复标识

同步程序必须能在“内容 commit 已创建、index commit 尚未创建”时安全重跑，不能因为中断重复生成同一版本。

每个内容 commit 的 message 使用稳定格式，并至少带以下 trailer：

```text
mirror: publish <skill-dir>@<version>

Learnova-Community-Id: <community_id>
Learnova-Version: <version>
Learnova-Skill-Dir: <skill-dir>
```

正常情况下同步程序从 `index.json` 获取已有版本映射；如果上一次运行在更新 index 前中断，则从 Git 历史中的上述 trailer 恢复该版本对应的 commit hash。

## 5. 同步行为约定

镜像 = 社区 APPROVED 技能集合的**纯投影**。

| 行为 | 约定 |
|---|---|
| 触发 | 审核通过事件或定时任务触发；允许节流合并，一轮同步处理期间积累的所有变化 |
| 上架 | 每个尚未镜像的新 APPROVED 版本按创建时间顺序处理：写入对应目录 → 创建内容 commit → 记录 commit hash |
| 下架/删除 | 技能在社区不再 APPROVED → 从工作区删除目录；git 历史保留，满足备份定位 |
| commit 粒度 | 每个新 APPROVED 版本一个内容 commit；下架删除可在本轮统一形成一个状态 commit；最后再生成一个 index commit |
| index.json | 所有内容 commit 完成后全量生成；其中 `versions[].commit` 只引用已经存在的内容 commit |
| push 粒度 | 一轮同步完成后统一 push，一轮可以有多个 commit，但通常只 push 一次 |
| 失败处理 | 同步失败不阻塞审核；下一轮触发时重试 |

### 5.1 一轮同步的完整流程

```text
读取所有 APPROVED 版本
        ↓
读取 index.json + Git 历史中的镜像版本标识
        ↓
找出尚未镜像的 APPROVED 版本
        ↓
按 created_at / community_id / version 稳定排序
        ↓
逐版本：
  从权威归档复制 Skill
  → 校验
  → 更新 <skill-dir>/
  → git commit
  → 记录 commit hash
        ↓
对账当前应存在的 Skill
  → 删除已下架目录
  → 如有变化创建状态 commit
        ↓
全量生成 index.json
  → 校验所有 versions[].commit 均存在
  → 如有变化创建 index commit
        ↓
git push main
```

如果一轮没有任何内容、下架状态或索引变化，则不创建 commit，也不 push。
