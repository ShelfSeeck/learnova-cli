# CLI 规范

Learnova CLI 的行为规范。实现架构（模块划分与设计模式）另行约定后补充。

## 1. 运行形态

- Python 3.10+，纯标准库，零第三方运行时依赖。
- 以 agent skill 形式分发（`npx skills` 安装），SKILL.md 指导 agent 调用脚本。
- 分发目录：`skills/learnova-cli/`。
- 入口：`skills/learnova-cli/scripts/learnova.py`；Windows 提供 PowerShell 薄入口，POSIX 提供 sh 薄入口。
- 脚本之间无隐式共享状态，必要信息通过 `~/.learnova/` 下的本地文件交换。

## 2. 命令面

```
learnova-cli init                                   # 首次初始化引导（幂等）
learnova-cli search <关键词> [--tag t] [--sort popular|newest|likes] [--offline]
learnova-cli info <技能>                            # 元数据 + 版本列表 + README
learnova-cli install <技能>[@版本] [--project] [--agent a] [--force]
learnova-cli list [--project]
learnova-cli uninstall <技能> [--project]
learnova-cli upgrade [<技能>|--all]
learnova-cli login | logout | whoami
learnova-cli publish <目录> --version x.y.z --changelog "..."
learnova-cli status [<技能>]                        # 提交版本的审核状态
learnova-cli config [get] | config set <键> <值>
```

全局参数：`--json` 结构化输出。

### 默认行为

| 场景 | 约定 |
|---|---|
| 安装目标 | 默认用户级，装到**所有检测到的 agent**；`--project` 装项目级；`--agent` 限定目标 |
| 同名冲突 | 默认报错，显式 `--force` 才覆盖 |
| 升级 | 仅当索引中存在更高版本号才重装；同版本内容不变 |
| 已安装清单 | 不维护本地记录，`list`/`uninstall`/`upgrade` 扫描 agent skills 目录重建状态 |
| Scope 配置 | `install_scope` **只决定 install 的默认层级**；`list`/`uninstall`/`upgrade` 默认始终操作用户级，只有显式 `--project` 才操作项目级 |
| 交互 | 默认非交互；`login`、`init` 及 `publish` 的版本号/changelog 由用户在终端亲自确认，agent 不代输凭证 |

## 3. agent 适配

映射表为数据而非代码分支，新增 agent 即新增一行。

| agent | 检测依据（目录存在） | 用户级 skills | 项目级 skills |
|---|---|---|---|
| claude | `~/.claude/` | `~/.claude/skills/` | `.claude/skills/` |
| opencode | `~/.agents/` | `~/.agents/skills/` | `.agents/skills/` |
| codex | `~/.codex/` | `~/.codex/skills/` | `.agents/skills/` |

项目级路径以当前工作目录为根。opencode 项目级路径实现时以实测为准。

## 4. 配置与本地存储

存放于 `~/.learnova/`：

| 文件 | 内容 | 权限 |
|---|---|---|
| `config.json` | 下表配置项 | 600 |
| `auth.json` | 登录凭证（refresh token 等） | 600 |
| `index_cache.json` | 社区索引离线缓存 | 600 |

写盘一律临时文件 + 原子替换。

### 配置项

| 键 | 默认值 | 说明 |
|---|---|---|
| `backend_url` | 发行时内置 | 后端 API 根地址 |
| `mirror_repo_url` | `https://git.nju.edu.cn/learnova/skills-mirror` | git 镜像仓地址；用户配置可覆盖 |
| `telemetry` | `auto`（跟随登录态） | `off` 时登录也不上报 |
| `agents` | `auto`（按目录检测） | 可限定为子集，如 `["claude", "codex"]` |
| `install_scope` | `user` | 默认安装层级 |
| `index_cache_ttl` | `86400` | 离线缓存有效期（秒） |

## 5. 检索策略

- 最终形态默认调后端 API（与网页端一致的实时检索）；当前后端数据源尚未接入，未配置后端时直接使用镜像源。
- 后端不可达时降级镜像源；镜像源优先使用未过期的本地 index 缓存，需要时联网刷新。
- `search --offline` 为**严格离线**：只读取已有 `index_cache.json`，不访问后端或 GitLab；没有缓存时直接报错。
- 镜像检索按 name/display_name/description/tags 过滤，按 likes/downloads 快照排序。
- 使用缓存时输出须标注 `generated_at` 并提示数据可能陈旧；缓存超过 `index_cache_ttl` 同样提示。

## 5.1 Skill 目录安全边界

- CLI 接受的技能目录标识必须是单个相对路径段；禁止空值、`.`、`..`、`/`、`\\` 与 NUL。
- 所有安装、覆盖与删除目标在执行文件操作前必须确认仍位于对应 skills 根目录内。
- 镜像 archive 只接受普通文件与目录；拒绝绝对路径、路径穿越、符号链接/硬链接及越出目标目录的成员。
- 下载后的技能包必须包含可解析且带 `name` 的 `SKILL.md`；若 `SKILL.md` 自带 `version`，其值必须与 index 中所选版本一致。
- Learnova 安装时在技能目录写入 `.learnova-install.json` 记录实际 `dir/version`，不要求第三方 Skill 为 Learnova 修改自身 frontmatter；`list/upgrade` 优先使用该 sidecar，并兼容旧安装从 `SKILL.md` 读取版本。

## 6. 遥测

- 只收 `install` 事件：技能目录名、版本、时间戳。
- 仅登录状态下上报；未登录完全静默。接受「下载量 ≠ 真实使用量」。
- 关闭优先级：环境变量 `LEARNOVA_TELEMETRY=0` > `config telemetry=off` > 默认（跟随登录态）。
- 工程要求：fire-and-forget，短超时，失败静默，永不阻塞或失败安装主流程。

## 7. 发布链路

```
login（用户亲自执行）
  → publish <目录>：本地打 zip → 上传为仓库条目 → 提交发布（version + changelog）
  → 进入社区审核流（与网页端一致）
  → status 查询审核状态与驳回备注
```

- 发布复用后端现有上传/发布接口；审核状态查询依赖后端提供的 publish-status 接口。
- 发布成功后技能内容经审核上架，由镜像同步进入 git 仓，CLI 用户即可检索安装。
