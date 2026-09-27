# Learnova CLI 设计文档

Learnova 技能社区的分发工具，面向使用个人 agent（Claude Code、OpenCode、Codex CLI 等）的用户，不依赖 Learnova 网页端。

## 定位

- 以 agent skill 形式分发，经 `npx skills` 安装，功能由纯 Python 脚本实现。
- 两个外部依赖：校内 git 镜像仓（skill 文件与离线索引）、Learnova 后端 API（在线检索、账号、发布、遥测）。任一不可达时核心安装能力仍可用。

## 文档索引

| 文档 | 内容 | 读者 |
|---|---|---|
| [mirror-spec.md](mirror-spec.md) | git 镜像仓规范：目录布局、命名规则、index.json schema、同步行为约定 | 镜像维护者、CLI 开发者 |
| [operations.md](operations.md) | 运行与维护手册：远程 Git 镜像仓、同步凭证、服务器准备、镜像链路验收与日常维护 | 项目维护者、部署人员 |
| [cli-spec.md](cli-spec.md) | CLI 规范：命令面、配置与本地存储、agent 适配、检索与遥测、发布链路 | CLI 开发者、使用者 |
| [architecture.md](architecture.md) | 实现架构：命令层、输出契约、数据源抽象、安装引擎、错误模型 | CLI 开发者 |
| [maintenance.md](maintenance.md) | 维护手册：加新命令/agent/数据源的步骤、契约变更规则、错误码登记 | CLI 开发者 |
| [ci.md](ci.md) | CI 设计：测试分层、fixture、变量清单、流水线（实施向） | CI 实施者 |
