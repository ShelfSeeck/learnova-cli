"""幂等初始化；终端确认前不写入配置。"""

import json
import sys

import agents
import auth
from core.errors import CliError
import store


def initialize(ctx) -> dict:
    detected = agents.detect()
    config = {key: ctx.config.get(key, value) for key, value in store.DEFAULTS.items()}
    for key in ("backend_url", "mirror_repo_url"):
        if config[key]:
            auth.validate_base_url(config[key])
    selected = None if config["agents"] == "auto" else config["agents"]
    targets = agents.resolve_targets(selected, config["install_scope"] == "project", ctx.cwd)
    summary = {"config": config, "detected_agents": detected,
               "targets": [{"path": str(target.path), "agents": list(target.agents)} for target in targets]}
    if store.CONFIG_PATH.exists():
        return {"status": "completed", "initialized": True, **summary}
    if not targets:
        return {"status": "needs_input", "kind": "configuration", **summary,
                "questions": [{"key": "agents", "question": "未检测到 agent；请安装受支持的 agent，或通过 config set agents 配置目标后重新运行 init"}]}
    question = "确认保存以上配置？请在本人终端运行 learnova-cli init 并确认"
    if not auth.terminal_available():
        return {"status": "needs_input", "kind": "configuration", **summary,
                "questions": [{"key": "confirm", "question": question}]}
    try:
        print("初始化配置:\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n确认保存？[y/N] ",
              end="", file=sys.stderr, flush=True)
        confirmation = input()
    except (EOFError, KeyboardInterrupt):
        raise CliError("BAD_USAGE", "初始化已取消；请在终端重新运行 learnova-cli init", exit_code=2) from None
    if confirmation.strip().lower() not in {"y", "yes"}:
        return {"status": "needs_input", "kind": "configuration", **summary,
                "questions": [{"key": "confirm", "question": question}]}
    store._write_json(store.CONFIG_PATH, config)
    ctx.config = config
    return {"status": "completed", "initialized": True, **summary}
