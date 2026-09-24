# Learnova CLI

Learnova 技能社区的命令行入口，以 agent skill 形式分发。

## 安装

```bash
npx skills add <owner>/learnova-cli@learnova-cli
```

安装后由 agent 按 SKILL.md 指引调用，也可以直接手动使用：

```bash
python scripts/learnova.py --help
```

## 命令

| 命令 | 说明 |
|---|---|
| `init` | 首次初始化：确认后端/镜像地址、遥测开关，检测已安装的 agent |
| `search <关键词>` | 搜索社区技能（`--tag`、`--sort`、`--offline`） |
| `info <技能名>` | 技能详情、版本列表、README |
| `install <技能名>[@版本]` | 安装到 agent 的 skills 目录（默认用户级、全部检测到的 agent） |
| `list` / `uninstall` / `upgrade` | 已安装技能管理 |
| `login` / `logout` / `whoami` | Learnova 账号 |
| `publish <目录>` | 发布技能到社区（进入审核流） |
| `status` | 查询已提交版本的审核状态 |
| `config set <键> <值>` | 修改配置 |

## 配置与数据

存放在 `~/.learnova/`：

- `config.json`：后端地址、镜像地址、遥测开关、目标 agent、安装层级、索引缓存 TTL
- `auth.json`：登录凭证（权限 600）
- `index_cache.json`：社区索引离线缓存

## 遥测与隐私

仅在登录状态下，安装技能时上报一次安装事件用于社区活跃度统计。不产生其他上报，未登录时完全静默。关闭方式：

```bash
python scripts/learnova.py config set telemetry off
# 或
export LEARNOVA_TELEMETRY=0
```

## 运行要求

Python 3.10+，无第三方依赖。
