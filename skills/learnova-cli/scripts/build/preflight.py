"""Read-only build readiness checks; no service calls or secret values in output."""
import importlib.util
import os
from pathlib import Path
from core.errors import CliError
from . import config


def inspect(root, input_path, mode='agent', parser=None):
    source = Path(input_path).absolute()
    if not source.is_file() or source.suffix.lower() not in ('.md', '.markdown', '.pdf'):
        raise CliError('BUILD_INPUT_INVALID', '输入必须是存在的 Markdown 或 PDF 文件', 2)
    cfg = config.load(root)
    if parser:
        cfg['parser'] = parser
    config.validate(cfg)
    issues, dependencies = [], {}
    info = {'path': str(source), 'format': source.suffix.lower().lstrip('.'), 'bytes': source.stat().st_size}
    presence = {}
    if source.suffix.lower() == '.pdf':
        dependencies['pypdf'] = importlib.util.find_spec('pypdf') is not None
        if cfg['parser'] == 'local':
            dependencies['gradio_client'] = importlib.util.find_spec('gradio_client') is not None
        missing = [name for name, available in dependencies.items() if not available]
        if missing:
            issues.append({'kind': 'dependency', 'packages': missing,
                           'action': '在运行 CLI 的虚拟环境中用 uv 安装所列依赖'})
        if dependencies['pypdf']:
            from pypdf import PdfReader
            try:
                reader = PdfReader(source)
                info['pages'] = len(reader.pages)
                samples = sorted({0, len(reader.pages) // 2, len(reader.pages) - 1}) if reader.pages else []
                info['sampled_text_layer'] = any((reader.pages[i].extract_text() or '').strip() for i in samples)
            except Exception:
                raise CliError('BUILD_INPUT_INVALID', '无法读取 PDF 页信息，请检查文件是否完整或加密') from None
        keys = [cfg['parser'] + '_endpoint'] + (['remote_key_env'] if cfg['parser'] == 'remote' else [])
        missing = [key for key in keys if not cfg[key]]
        if cfg['parser'] == 'remote':
            available = bool(cfg['remote_key_env'] and os.environ.get(cfg['remote_key_env']))
            presence['parser'] = available
            if cfg['remote_key_env'] and not available:
                missing.append('remote_key_env')
        if missing:
            issues.append({'kind': 'configuration', 'scope': 'parser', 'fields': missing,
                           'action': '选择远程 MinerU 预设或已有本地 Gradio 服务；环境变量须对当前 CLI 进程可见'})
    if mode == 'auto':
        keys = ('llm_base_url', 'llm_model', 'llm_key_env')
        available = bool(cfg['llm_key_env'] and os.environ.get(cfg['llm_key_env']))
        presence['model'] = available
        missing = [key for key in keys if not cfg[key] or (key == 'llm_key_env' and not available)]
        if missing:
            issues.append({'kind': 'configuration', 'scope': 'llm', 'fields': missing,
                           'action': '配置自动模式模型服务，或使用无需额外模型凭证的 agent 模式'})
    return {'ready': not issues, 'input': info, 'mode': mode, 'parser': cfg['parser'],
            'dependencies': dependencies, 'credential_presence': presence, 'issues': issues,
            'credential_note': '仅检查当前进程中的变量是否可见，不验证 Token；其他终端 export 不会自动传入此进程'}
