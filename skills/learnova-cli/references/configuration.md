# 配置、镜像与缓存

正常的 Skill 发现和安装不需要先修改配置。客户端默认使用正式 Learnova skills mirror，并在本地维护索引缓存。

## 配置位置

客户端状态位于 `~/.learnova/`，主要包括：

- `config.json`：客户端配置。
- `auth.json`：绑定后端来源的刷新凭证，权限为 600；不要读取、展示或分发其内容。
- `index_cache.json`：社区索引缓存。

## 常用配置

```bash
python "<skill-dir>/scripts/learnova.py" config --json
python "<skill-dir>/scripts/learnova.py" config get --json
python "<skill-dir>/scripts/learnova.py" config set <key> <value> --json
```

`config` 和 `config get` 均返回完整配置，不支持按键读取。当前相关配置包括：

- `mirror_repo_url`：Git 镜像仓地址。
- `backend_url`：默认 `https://ta.njunova.com`，可通过 `config set backend_url <地址>` 覆盖；旧版初始化保存的空地址自动使用默认值。
- `agents`：限制自动检测的 Agent 集合。
- `install_scope`：install 的默认安装层级。
- `index_cache_ttl`：索引缓存有效期，单位秒。
- `telemetry`：`auto` 跟随登录态，`off` 关闭。
- `telemetry_endpoint`：后端部署并启用专用事件端点后配置的同源绝对路径，默认空；未配置不发送安装事件，不猜测路由。

## 镜像与离线搜索

有可用登录凭证时优先在线源，否则使用镜像。后端连接、服务端或认证错误可回退镜像；技能不存在、响应非法或包校验失败会保留错误。镜像优先使用有效缓存，需要刷新时读取 `index.json`。缓存绑定镜像来源，切换地址后须获取新索引。

```bash
python "<skill-dir>/scripts/learnova.py" search "<keyword>" --offline --json
```

`--offline` 是严格离线模式，只读取已有缓存。没有缓存时直接失败，不应静默联网。

搜索或 `info` 的结构化结果会包含 `source`、`generated_at` 和 `stale`，Agent 可以据此判断数据来源和缓存是否陈旧。

在线源用镜像映射保持技能目录名稳定，尚未进入镜像的在线条目暂不列为可安装结果；可通过 `backend_total`、`unmapped_total` 了解差额。登录和后端地址变更见 [accounts.md](accounts.md)。

## 遥测

后端部署并启用专用端点后，只在登录且配置端点时，成功安装可能发送 `{event:"install",skill,version,ts}`。关闭优先级为 `LEARNOVA_TELEMETRY=0`、`telemetry=off`、登录态。独立短命进程仅通过私有标准输入接收当前访问凭证，不使用凭证参数、环境变量或事件文件，也不刷新登录态；缺少或过期凭证即丢弃事件。发送失败不影响安装。端点尚未部署或启用时保持配置为空。

配套接口路径为 `/community/install-events`。发送进程只从当前镜像已有缓存精确匹配目录与版本，将公共 `community_id` 作为查询定位参数，事件正文仍只有上述四字段。缓存缺失、错源或映射不唯一时不发送，也不联网猜测身份。

构建配置独立于社区客户端配置。项目覆盖用户默认值；任务创建时保存快照，后续须用 `build reconfigure` 显式更改。解析与模型凭证只保存环境变量名，详见 [building.md](building.md)。
