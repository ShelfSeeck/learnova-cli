"""Independent, lazily loaded build configuration."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import store
from core.errors import CliError

# Retired local fields stay in snapshots/output so existing V4 seals remain valid.
DEFAULTS = {'parser': 'remote', 'remote_endpoint': '', 'remote_key_env': '', 'local_endpoint': '',
            'llm_base_url': '', 'llm_model': '', 'llm_key_env': '', 'min_tokens': 200,
            'max_tokens': 2000, 'max_depth': 4, 'read_limit': 4000,
            'parser_pages_per_part': 200, 'parser_max_part_bytes': 200 * 1024 * 1024,
            'parser_language': 'ch', 'parser_formula': True, 'parser_table': True, 'parser_ocr': False,
            'remote_model_version': 'vlm', 'parser_request_timeout': 30, 'parser_upload_timeout': 120,
            'parser_download_timeout': 120, 'parser_archive_bytes': 100 * 1024 * 1024,
            'parser_expanded_bytes': 256 * 1024 * 1024, 'parser_member_bytes': 64 * 1024 * 1024,
            'local_api_name': '/convert_to_markdown_stream', 'local_language': 'ch (Chinese, English, Chinese Traditional)', 'local_backend': 'pipeline',
            'local_result_timeout': 300}
CHUNKING_KEYS = frozenset(('min_tokens', 'max_tokens', 'max_depth', 'llm_base_url', 'llm_model', 'llm_key_env'))
PARSING_KEYS = frozenset(DEFAULTS) - CHUNKING_KEYS - {'read_limit'}
PRESETS = {'mineru': {'parser': 'remote', 'remote_endpoint': 'https://mineru.net',
                      'remote_key_env': 'MINERU_API_KEY', 'parser_ocr': True}}
RETIRED_PARSER_KEYS = frozenset(('local_endpoint', 'local_api_name', 'local_language', 'local_backend', 'local_result_timeout'))


def require_http(data):
    if data['parser'] != 'remote':
        raise CliError('BUILD_CONFIG_INVALID', '只支持 MinerU V4 HTTP；请设置 parser=remote 与 remote_endpoint，已有任务用 build reconfigure 更新', 2)


def validate(data):
    if set(data) != set(DEFAULTS):
        raise CliError('BUILD_CONFIG_INVALID', '未知或缺失构建配置项', 2)
    for key, default in DEFAULTS.items():
        value = data[key]
        if isinstance(default, bool):
            if type(value) is not bool:
                raise CliError('BUILD_CONFIG_INVALID', f'无效布尔值: {key}', 2)
        elif isinstance(default, int):
            if type(value) is not int or value < (0 if key in ('min_tokens', 'max_depth') else 1) or value > (1024 * 1024 * 1024 if key.endswith('_bytes') else 1000000):
                raise CliError('BUILD_CONFIG_INVALID', f'无效配置: {key}', 2)
        elif not isinstance(value, str):
            raise CliError('BUILD_CONFIG_INVALID', f'无效配置: {key}', 2)
        elif key.endswith('_key_env') and value and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
            raise CliError('BUILD_CONFIG_INVALID', f'密钥引用必须是环境变量名: {key}', 2)
    for key in ('remote_endpoint', 'local_endpoint', 'llm_base_url'):
        if data[key]:
            try:
                url = urlsplit(data[key])
                valid = (url.scheme in ('http', 'https') and bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment and '?' not in data[key] and '#' not in data[key])
                _ = url.port
            except ValueError:
                valid = False
            if not valid:
                raise CliError('BUILD_CONFIG_INVALID', f'无效服务地址: {key}', 2)
    for key in ('parser_language', 'remote_model_version'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', data[key]):
            raise CliError('BUILD_CONFIG_INVALID', f'无效配置: {key}', 2)
    require_http(data)
    if data['min_tokens'] > data['max_tokens']:
        raise CliError('BUILD_CONFIG_INVALID', '无效切片阈值', 2)
    return data


def load(root, overrides=None):
    return validate({**DEFAULTS, **store._read_json(store.LEARNOVA_HOME / 'build-config.json'),
                     **store._read_json(Path(root) / '.learnova' / 'build-config.json'), **(overrides or {})})


def missing(data, service):
    if service != 'llm':
        require_http(data)
    keys = {'remote': ('remote_endpoint',), 'llm': ('llm_base_url', 'llm_model', 'llm_key_env')}[service]
    if service == 'remote' and data['remote_key_env']:
        keys += ('remote_key_env',)
    return [key for key in keys if not data[key] or (key.endswith('_key_env') and not os.environ.get(data[key]))]


def parse_settings(settings):
    values = {}
    for setting in settings:
        key, separator, value = setting.partition('=')
        if not separator or not key or key in values:
            raise CliError('BAD_USAGE', '配置需使用不重复的 KEY=VALUE', 2)
        values[key] = value
    return values


def parse_value(key, value):
    if key in RETIRED_PARSER_KEYS:
        raise CliError('BUILD_CONFIG_INVALID', '本地 Gradio 配置已停用；请设置 remote_endpoint 和可选 remote_key_env', 2)
    if key not in DEFAULTS:
        raise CliError('BUILD_CONFIG_INVALID', f'未知配置项: {key}', 2)
    if isinstance(DEFAULTS[key], bool):
        if isinstance(value, bool):
            return value
        if value not in ('true', 'false'):
            raise CliError('BUILD_CONFIG_INVALID', f'无效布尔值: {key}', 2)
        return value == 'true'
    if isinstance(DEFAULTS[key], int):
        try:
            return int(value)
        except ValueError as exc:
            raise CliError('BUILD_CONFIG_INVALID', f'无效整数: {key}', 2) from exc
    return value


def set_value(root, key, value, user=False):
    result = set_values(root, {key: value}, user)
    return {'key': key, 'value': result['values'][key]}


def preset_values(name):
    if name not in PRESETS:
        raise CliError('BUILD_CONFIG_INVALID', '未知构建预设', 2)
    return dict(PRESETS[name])


def set_values(root, values, user=False):
    values = {key: parse_value(key, value) for key, value in values.items()}
    path = store.LEARNOVA_HOME / 'build-config.json' if user else Path(root) / '.learnova' / 'build-config.json'
    data = store._read_json(path)
    data.update(values)
    if user:
        validate({**DEFAULTS, **data})
    else:
        load(root, data)
    store._write_json(path, data)
    return {'values': values, 'scope': 'user' if user else 'project'}
