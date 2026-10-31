"""Independent, lazily loaded build configuration."""
import re
from pathlib import Path
from urllib.parse import urlsplit
import store
from core.errors import CliError

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
    for key in ('parser_language', 'remote_model_version', 'local_backend'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', data[key]):
            raise CliError('BUILD_CONFIG_INVALID', f'无效配置: {key}', 2)
    if not data['local_language'] or len(data['local_language']) > 128 or any(ord(c) < 32 for c in data['local_language']):
        raise CliError('BUILD_CONFIG_INVALID', '无效配置: local_language', 2)
    if not re.fullmatch(r'/[A-Za-z0-9_/-]{1,128}', data['local_api_name']):
        raise CliError('BUILD_CONFIG_INVALID', '无效配置: local_api_name', 2)
    if data['parser'] not in ('remote', 'local') or data['min_tokens'] > data['max_tokens']:
        raise CliError('BUILD_CONFIG_INVALID', '无效解析器或切片阈值', 2)
    return data


def load(root):
    return validate({**DEFAULTS, **store._read_json(store.LEARNOVA_HOME / 'build-config.json'),
                     **store._read_json(Path(root) / '.learnova' / 'build-config.json')})


def parse_value(key, value):
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
    value = parse_value(key, value)
    path = store.LEARNOVA_HOME / 'build-config.json' if user else Path(root) / '.learnova' / 'build-config.json'
    data = store._read_json(path)
    data[key] = value
    validate({**DEFAULTS, **data} if user else {**load(root), **data})
    store._write_json(path, data)
    return {'key': key, 'value': value}
