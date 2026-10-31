---
name: learnova-cli
description: Learnova 技能社区的发现与分发入口。当用户明确要求搜索、查看、安装、升级或卸载 Learnova 社区的 agent skill，或需要将 PDF、Markdown 构建为知识参考型 Skill，或当前任务需要额外的专用 Skill 能力而现有能力不足时使用。支持 Claude Code、OpenCode、Codex CLI。
---

# Learnova CLI

Learnova 可发现和管理社区 Skill，也可从 PDF 或 Markdown 构建本地知识参考型 Skill。由宿主 Agent 理解意图、组织操作并作语义判断；仅询问真正缺少的信息。当前社区检索与安装使用 Git 镜像；账号、在线后端检索和发布尚未实现。

## 一句话开始

用户可以直接表达意图，无需记命令：

- “这个 CLI 能做什么，怎么用？”：介绍能力和示例，不因此执行写入。
- “找一个适合这个任务的 Skill”或“看看这个候选”：按需搜索、查看并判断，读 [发现指南](references/discovery.md)。
- “安装这个 Skill”“看看安装了什么”“升级或卸载它”：读 [安装与本地管理](references/installation.md)。
- “把这本课本生成一个方便查阅和问答的 Skill”“继续上次没完成的生成”或“打包刚生成的 Skill”：读 [构建指南](references/building.md)。打包是本地生成 ZIP，不是发布。

沿用对话中明确的文件、项目和输出选择；任务标识和命令输出由 Agent 处理。需要找旧任务时只检查相关项目的任务记录，确有歧义才询问。项目明确且未指定输出时，说明默认项目路径并继续；输出含义不明或需要覆盖现有目录时才询问。使用生成的 Skill 时让宿主加载其路径，或明确读取其 `SKILL.md`；不要假设自动实时重载。

## 调用入口

当前 `SKILL.md` 所在目录就是运行时根目录。执行脚本时以该目录为基准，不要假设当前 shell 已切换到 Skill 目录。

所有调用一律使用 `--json` 获取结构化输出：

```bash
python "<skill-dir>/scripts/learnova.py" <command> [args] --json
```

Windows PowerShell 使用：

```powershell
& "<skill-dir>/learnova-cli.ps1" <command> [args] --json
```

## 按需读取

只读取当前任务需要的文档：

- 需要寻找或判断候选 Skill：读取 [references/discovery.md](references/discovery.md)。
- 需要安装、查看本地安装、升级或卸载：读取 [references/installation.md](references/installation.md)。
- 需要从用户任务出发完成“发现 → 判断 → 安装 → 使用”的完整流程：读取 [references/agent-workflow.md](references/agent-workflow.md)。
- 需要从 PDF 或 Markdown 构建知识参考型 Skill：读取 [references/building.md](references/building.md)。
- 只需要查询准确命令和参数：读取 [references/commands.md](references/commands.md)。
- 需要了解镜像、缓存、离线模式或客户端配置：读取 [references/configuration.md](references/configuration.md)。

不要预先读取全部 reference；根据当前步骤逐级加载。

## 基本边界

- 默认非交互，优先让 Agent 自己完成检索、详情读取和安装。
- 同名 Skill 冲突默认停止；只有用户明确要求覆盖时才使用 `--force`。
- 尚未实现的 `init`、`login`、`logout`、`whoami`、`publish`、`status` 不要调用。
