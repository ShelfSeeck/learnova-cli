# 配置、镜像与缓存

正常的 Skill 发现和安装不需要先修改配置。客户端默认使用正式 Learnova skills mirror，并在本地维护索引缓存。

## 配置位置

客户端状态位于 `~/.learnova/`，主要包括：

- `config.json`：客户端配置。
- `index_cache.json`：社区索引缓存。

## 常用配置

```bash
python "<skill-dir>/scripts/learnova.py" config --json
python "<skill-dir>/scripts/learnova.py" config get <key> --json
python "<skill-dir>/scripts/learnova.py" config set <key> <value> --json
```

当前相关配置包括：

- `mirror_repo_url`：Git 镜像仓地址。
- `agents`：限制自动检测的 Agent 集合。
- `install_scope`：install 的默认安装层级。
- `index_cache_ttl`：索引缓存有效期，单位秒。
- `telemetry`：遥测开关；当前没有实际遥测发送端点。

## 镜像与离线搜索

正常搜索优先使用有效的本地索引缓存；需要刷新时从 Git 镜像读取 `index.json`。

```bash
python "<skill-dir>/scripts/learnova.py" search "<keyword>" --offline --json
```

`--offline` 是严格离线模式，只读取已有缓存。没有缓存时直接失败，不应静默联网。

搜索或 `info` 的结构化结果会包含 `source`、`generated_at` 和 `stale`，Agent 可以据此判断数据来源和缓存是否陈旧。

构建配置独立于社区客户端配置。项目覆盖用户默认值；任务创建时保存快照，后续须用 `build reconfigure` 显式更改。解析与模型凭证只保存环境变量名，详见 [building.md](building.md)。
