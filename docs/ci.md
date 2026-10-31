# CI 与验收

正式主仓使用仓根 `.gitlab-ci.yml`。`.github/workflows/` 是可选副本的等价检查；同步 GitHub 不是开发或发行前提。流水线定义已提供，实际运行记录和 runner 可用性须以托管平台为准。

## 必过检查

```bash
python tests/run_ci.py
```

统一入口依次运行 `python -m unittest discover tests -v`、`python tests/t1_mirror_flow.py`，再验证 Python、POSIX sh 和可用 PowerShell 薄入口的成功 JSON、错误 JSON 与退出码。测试只用 stdlib、本地 HTTP fixture 和临时目录，无需后端凭证。缺少必需的平台工具会失败，不把零用例或环境缺失视为通过。

T0 包括纯逻辑、账号、在线源、发布、遥测和构建回归。T1 覆盖镜像检索、详情、固定版本安装、冲突、跨 Agent 升级、清单、卸载和严格离线零网络。`test_distribution.py` 另验证随包引用完整，以及复制到三种 Agent 项目布局后的独立运行。

## 主仓作业

| 作业 | 条件 | 内容 |
|---|---|---|
| `linux` | 分支或合并请求 | uv 提供 Python 3.10–3.13，运行统一入口 |
| `windows` | `LEARNOVA_WINDOWS_RUNNER_TAG` 非空 | 对应原生 Windows runner；四个 Python 版本，必须验证 PowerShell |
| `macos` | `LEARNOVA_MACOS_RUNNER_TAG` 非空 | 对应原生 macOS runner；四个 Python 版本 |
| `backend-integration` | 后端 opt-in 与地址均配置 | 真实 T2，不阻塞合并 |
| `mirror-smoke` | schedule 且镜像 opt-in | 真实公开镜像检索和安装；独立于必过检查 |

Windows/macOS 标签变量必须指向已配置的原生 runner，预装 uv、git，Windows 还需 `pwsh`。未配置标签时不创建相应作业，不表示该平台已通过。Linux 作业安装固定版本 uv；测试本身仍用 `unittest`，不安装测试框架。

可在真实平台手动验收：

```bash
python tests/run_ci.py --expected-platform Linux
python tests/run_ci.py --expected-platform Darwin
```

Windows PowerShell：

```powershell
python tests/run_ci.py --expected-platform Windows --require-powershell
```

Linux 上运行 PowerShell 只能证明该平台的入口行为，不能替代原生 Windows 验收。

## 可选真实后端 T2

`python tests/t2_backend_flow.py` 直接测试账号服务和在线源，不自动向交互式 `login` 输送密码。须由维护者配置专用低权限测试账号；普通用户仍由本人终端登录。

| 变量 | 用途 |
|---|---|
| `LEARNOVA_RUN_BACKEND_TESTS=1` | 显式启用真实后端验收 |
| `LEARNOVA_TEST_BACKEND_URL` | 测试 API 根地址，如 `https://api.example.invalid` |
| `LEARNOVA_TEST_EMAIL` | 测试邮箱；兼容旧名 `LEARNOVA_TEST_USERNAME` |
| `LEARNOVA_TEST_PASSWORD` | 受保护、masked 的测试密码 Secret |
| `LEARNOVA_TEST_MIRROR_URL` | 可选镜像覆盖，用于稳定社区 ID 映射 |
| `LEARNOVA_TEST_ALLOW_WRITES=1` | 另行显式启用唯一发布 fixture，默认关闭 |

默认只读社区和发布内容：登录、验证账号、在线搜索、详情、固定版本下载至临时目录、读取本人审核状态、退出。凭证只在内存中使用，禁止输出或写入文件。缺少开关、地址或凭证返回 `not_run` 和退出码 2；没有映射可安装结果会失败，不能视为成功。

写入模式只创建本次 UUID 命名的合成 Skill，提交待审核并查询状态；仅清理本次响应中记录的精确资源 ID，删除前再次核对名称和所有者，不选择或修改已有同名内容。失败后若无法确认安全清理，作业失败并需操作员检查专用测试账号。不要对普通账号启用写入模式。

真实 T2 不猜测遥测路由。后端部署并启用专用事件接口后再配置客户端端点，未配置时不发送；仅 fixture 测试证明发送协议、开关、非阻塞生命周期与访问凭证失效行为，不能当作生产计数验收。

## 镜像冒烟与分发

`LEARNOVA_RUN_MIRROR_SMOKE=1 python tests/mirror_smoke.py` 在独立临时 HOME 中搜索当前镜像、动态选择一个较小的当前版本、查看详情、固定版本安装、列出并卸载。可用 `LEARNOVA_TEST_MIRROR_URL` 覆盖地址；不依赖某个固定真实 Skill，不执行下载 Skill 的说明。空目录结果或接口失败会失败。

镜像同步服务已部署；此处只核查现有服务生成的公开镜像，不重复部署。生产 timer 的运行记录、审核后自动同步和托管 CI 的运行记录属于独立运行核查，不能从本地测试推断。

分发入口为：

```bash
npx skills@1.7.4 add https://git.nju.edu.cn/learnova/learnova-cli.git \
  --skill learnova-cli --agent codex --copy --yes
```

维护者验收时分别选择 `codex`、`opencode`、`claude-code`，在各自全新临时项目中执行；本地候选也用相同参数安装。校验安装后实际入口和随包文件，而非只运行仓库源码。第三方安装器使用固定且已核验的版本，隔离 HOME、npm 配置和缓存，禁用安装脚本与遥测，仅传必需环境变量；不得继承账号密钥、代理凭证或 `NODE_OPTIONS`。正式 main 的验收只证明当时远端分发包，不代表尚未推送的本地候选已发行。
