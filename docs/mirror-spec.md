# 镜像仓规范

Learnova 技能社区的 git 镜像，托管于校内 GitLab。承担三个职责：社区内容的备份、第三方 agent 的分发源、基于 git 历史的版本管理。

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

- 版本历史 = git 提交历史。diff、blame、回滚直接使用 git 能力，不另设版本目录。
- 安装指定旧版本：按 `versions[].commit` 调 GitLab archive API（`GET /projects/:id/repository/archive.tar.gz?sha=<commit>`），解压后取对应技能目录。
- 同一版本号内容不会变化（由社区审核流保证），升级判断只比较版本号。

## 5. 同步行为约定

镜像 = 社区 APPROVED 技能集合的**纯投影**。

| 行为 | 约定 |
|---|---|
| 触发 | 审核通过事件触发，节流合并：低频上限（默认 30 分钟最多推送一次），期间多次审核批量处理 |
| 上架 | 新版本 APPROVED → 更新对应技能目录工作区内容 |
| 下架/删除 | 技能在社区不再 APPROVED → 从工作区删除目录；git 历史保留，满足备份定位 |
| commit 粒度 | 每次同步的所有变更打成一个 commit，message 列出本次上架/更新/删除的技能与版本 |
| index.json | 每次同步重新全量生成，随同一 commit 提交 |
| 失败处理 | 同步失败不阻塞审核；下一轮触发时重试 |
