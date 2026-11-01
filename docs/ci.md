# CI 设计

本文是 learnova-cli 持续集成的实施规格，面向接手实现的人。读完应能直接动手，不需要再问设计问题。

正式主仓位于南大 GitLab。现有 `.github/workflows/` 是 GitHub Actions 参考实现，不能据此认定主仓已经或尚未接通 CI；须核实实际触发方式和运行记录。同步 GitHub 不是验收前提，其他 CI 平台可采用等价的测试矩阵和变量配置。

## 1. 目标与通过标准

验证工具在各目标环境（Linux / macOS / Windows + PowerShell，Python 3.10–3.13）下可用。

**通过标准：T0 + T1 全绿即算通过。** T1 覆盖「只依赖 git 镜像、后端完全不可达」的场景——工具的核心承诺是脱离后端也能检索和安装，这条必须在 CI 中卡住。T2（后端链路）尽力跑，失败不阻塞合并。

## 2. 测试分层（解耦设计）

| 层 | 内容 | 外部依赖 | 阻塞性 |
|---|---|---|---|
| T0 单元 | `core/` 纯逻辑：版本比较、frontmatter、index 过滤排序 | 无 | 必过 |
| T1 镜像链路 | 后端未配置时由 MirrorSource 完成 `search` / `info` / `install` / `list` / `uninstall` / `upgrade`；另验证 `search --offline` 零网络 | 仅本地 fixture（见 §3） | **必过** |
| T2 后端链路 | `login` / `publish` / `status` / 遥测 / 在线检索与降级 | 后端地址 + 测试账号（§4） | 不阻塞 |

分层原则：每层只依赖下一层已验证的东西；T1 不许出现任何对后端地址的访问（可在测试中断言零出站请求到 backend_url）。

## 3. Fixture：CI 不碰真实 GitLab

真实 git 镜像仓的可用性不应影响工具 CI。

- `tests/fixtures/mirror/`：微型镜像仓内容——2–3 个假技能目录（含 SKILL.md、references/）+ 手写 `index.json`（schema 见 docs/mirror-spec.md §3，含两个版本和各自 commit 占位）。
- `tests/fixtures/gitlab_stub.py`：本地 HTTP stub（`http.server`，约几十行），模拟两个端点：
  - `GET /.../raw/main/index.json` → 返回 fixture 的 index.json
  - `GET /.../repository/archive.tar.gz?sha=<commit>` → 动态打包 fixture 目录返回
- T1 流程：起 stub（随机端口）→ `LEARNOVA_HOME` 指向临时目录 → `config set mirror_repo_url http://127.0.0.1:<port>` → 跑命令断言结果。
- 真实 GitLab 冒烟另设 **schedule 触发**的独立工作流（如每日一次），不进 PR 检查。

## 4. 变量与密钥

| 名称 | 类型 | 用途 | 需要的层 |
|---|---|---|---|
| `LEARNOVA_TEST_BACKEND_URL` | Actions Variable | 测试后端地址 | T2 |
| `LEARNOVA_TEST_USERNAME` | Actions Secret | 后端测试账号 | T2 |
| `LEARNOVA_TEST_PASSWORD` | Actions Secret | 后端测试账号密码 | T2 |

T0/T1 不需要任何密钥——fork 仓 PR 拿不到 secrets 也必须能过主线检查。T2 作业在 secrets 缺失时自动 skip（`if: env.LEARNOVA_TEST_BACKEND_URL != ''`），不算失败。

测试账号由维护者在后端创建一个低权限普通账号，仅用于 CI。

## 5. 流水线参考实现（GitHub Actions）

触发：`push` 到 main + PR。另设 `schedule`（每日）跑真实 GitLab 冒烟。

```
job: test（矩阵）            # T0 + T1，通过标准所在
  matrix: os × python
    os: [ubuntu-latest, macos-latest, windows-latest]
    python: ["3.10", "3.11", "3.12", "3.13"]
  steps:
    - uv 安装指定 Python（astral-sh/setup-uv + uv python install）
    - T0: python -m unittest discover tests -v
    - T1: python tests/t1_mirror_flow.py（内部起 stub、跑全流程命令）
    - 仅 windows: pwsh 调用 skills/learnova-cli/learnova-cli.ps1 list 冒烟

job: backend-integration     # T2，不阻塞
  needs: test
  if: vars.LEARNOVA_TEST_BACKEND_URL != ''
  steps: login → publish（测试技能）→ status → 遥测断言
  失败仅告警，不影响 PR 状态（continue-on-error: true）

job: gitlab-smoke            # 仅 schedule
  对真实镜像仓跑 search/install 冒烟
```

CI 全程用 uv 提供 Python（与主项目一致），但**测试本身用 `python -m unittest` 裸跑**，不引入 pytest 或任何第三方包——与运行时零依赖原则一致。

## 6. 实施清单

- [x] 把 `tests/` 现有 pytest 风格测试改写为 `unittest`（纯断言迁移，无框架特性依赖）
- [x] 同步更新 `AGENTS.md` 硬规则中的测试命令为 `python -m unittest discover tests`
- [x] 编写 `tests/fixtures/mirror/`（假技能 × 2 + index.json）
- [x] 编写 `tests/fixtures/gitlab_stub.py`
- [x] 编写 `tests/t1_mirror_flow.py`：stub 生命周期管理 + 命令断言（search 出结果、install 落地文件正确、list 重建、upgrade 识别新版本、uninstall 清理）
- [x] 编写 `.github/workflows/ci.yml`（test 矩阵 + backend-integration）
- [x] 编写 `.github/workflows/gitlab-smoke.yml`（schedule）
- [ ] 核实正式主仓的 CI 触发与运行记录，完成 T0/T1 多平台矩阵与镜像冒烟验收
- [ ] 实现 `tests/test_backend*.py`；当前 backend-integration 仅为占位，零用例不代表后端链路通过
- [ ] 维护者在后端创建测试账号，配置 §4 的 Variable 与 Secrets
- [ ] Windows 作业实测 PowerShell 入口

## 7. 注意事项

- fixture 的 index.json 与 docs/mirror-spec.md 的 schema 必须同步演进；schema 变更时先改 fixture，CI 会立即暴露不一致。
- stub 只模拟 CLI 实际用到的两个端点，不要试图模拟完整 GitLab API。
- T1 中 `LEARNOVA_HOME` 必须指向临时目录，严禁污染 runner 的真实 home。
