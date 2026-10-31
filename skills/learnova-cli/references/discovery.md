# Skill 发现与判断

用于从 Learnova 社区寻找适合当前任务的 Skill，并在安装前判断候选项是否合适。

## 何时搜索

在以下情况使用 `search`：

- 用户明确要求查找 Learnova Skill。
- 当前任务需要专门能力，现有 Skill 无法覆盖或明显缺少领域知识、固定工作流或工具封装。

不要因为存在 Learnova 就机械搜索每个任务。普通能力已经足够时直接完成任务。

## 搜索

```bash
python "<skill-dir>/scripts/learnova.py" search "<keyword>" --json
```

可选参数：

- `--tag <tag>`：按标签筛选，可重复。
- `--sort popular|newest|likes`：选择排序方式。
- `--offline`：严格只读本地索引缓存，不联网刷新。

当前镜像搜索在客户端对 `name`、`display_name`、`description` 和 `tags` 做字段匹配，并使用镜像中的点赞、下载和发布时间快照排序。

## 查看候选详情

搜索结果只用于发现候选项。准备采用某个 Skill 前调用：

```bash
python "<skill-dir>/scripts/learnova.py" info <skill-dir-name> --json
```

`info` 返回展示信息、标签、统计快照、最新版本、历史版本以及当前 README。重点阅读：

- `description` 和 `tags`：快速判断领域是否匹配。
- `readme`：判断 Skill 的实际用途、约束和工作方式。
- `latest_version` / `versions`：需要固定版本或核对更新时使用。

不要只根据名称直接安装；候选存在歧义时先看 `info`。

安装与版本管理见 [installation.md](installation.md)。完整 Agent 流程见 [agent-workflow.md](agent-workflow.md)。
