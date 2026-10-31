# Agent 使用流程

本文件描述 Agent 如何把 Learnova 作为额外 Skill 能力的发现入口。

## 流程

1. 先判断当前已加载能力能否可靠完成用户任务。
2. 如果任务明显需要额外的专用 Skill，使用 `search` 搜索候选。
3. 对可能合适的候选调用 `info`，读取 description、tags 和 README。
4. 根据当前任务判断候选是否适用；必要时继续搜索其他关键词或比较多个候选。
5. 确定需要某个 Skill 后执行 `install`。
6. 安装完成后定位并读取新 Skill 的 `SKILL.md`。
7. 按新 Skill 的说明继续完成用户原始任务。

## 原则

- Learnova 是能力发现入口，不是每个任务都必须经过的前置步骤。
- 搜索结果是候选集合，不代表自动推荐；用 `info` 的 README 和元数据做下一步判断。
- 默认安装最新版。只有用户要求固定版本、兼容旧流程或有明确版本约束时指定 `@version`。
- 不为了“尝试一下”覆盖已有同名 Skill；出现冲突时保留现状并根据用户意图处理。
- 安装只是把文件放到目标 Agent 的 skills 目录。需要在当前任务继续使用时，显式读取新 Skill 的入口说明。

搜索和候选判断细节见 [discovery.md](discovery.md)，安装行为见 [installation.md](installation.md)。
