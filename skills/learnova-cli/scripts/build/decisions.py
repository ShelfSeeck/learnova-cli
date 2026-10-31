"""Bounded OpenAI-compatible decision adapter; model output is never trusted."""
import json
import os
from pathlib import Path
import net
from core.errors import CliError

PROMPT_PATH = Path(__file__).with_name('prompts').joinpath('decision.txt')
MAX_ROUNDS = 8
MAX_CONTEXT = 48000
MAX_RESPONSE = 65536


def _invalid():
    return CliError('BUILD_MODEL_RESPONSE_INVALID', '模型返回了无效判断或读取请求')


class Decision:
    def __init__(self, reader):
        self.reader = reader

    def decide(self, request, config_snapshot):
        key = os.environ.get(config_snapshot['llm_key_env'], '')
        if not key:
            raise CliError('BUILD_MODEL_REQUEST_FAILED', '模型凭证不可用')
        secrets = [key]
        parser_key = os.environ.get(config_snapshot.get('remote_key_env', ''), '')
        if parser_key:
            secrets.append(parser_key)
        messages = [{'role': 'system', 'content': PROMPT_PATH.read_text(encoding='utf-8')},
                    {'role': 'user', 'content': json.dumps(request, ensure_ascii=False)}]
        for _ in range(MAX_ROUNDS):
            if sum(len(m['content']) for m in messages) > MAX_CONTEXT:
                raise _invalid()
            try:
                response = net.request('POST', config_snapshot['llm_base_url'].rstrip('/') + '/chat/completions',
                                       {'model': config_snapshot['llm_model'], 'messages': messages,
                                        'temperature': 0, 'max_tokens': 8192 if request['kind'] == 'chunk_metadata_batch' else 2048}, token=key,
                                       timeout=30, max_bytes=MAX_RESPONSE)
            except CliError:
                raise CliError('BUILD_MODEL_REQUEST_FAILED', '模型请求失败；请检查配置后重试') from None
            try:
                content = response['choices'][0]['message']['content']
                if not isinstance(content, str) or len(content) > MAX_RESPONSE or any(secret in content for secret in secrets):
                    raise ValueError()
                answer = json.loads(content)
                if any(secret in json.dumps(answer, ensure_ascii=False) for secret in secrets):
                    raise ValueError()
                if not isinstance(answer, dict) or len(answer) != 1:
                    raise ValueError()
                if 'result' in answer and isinstance(answer['result'], dict):
                    return answer['result']
                read = answer['read']
                if not isinstance(read, dict) or set(read) - {'kind', 'start', 'end', 'limit'}:
                    raise ValueError()
                kind = read.get('kind')
                start = read.get('start', 0)
                limit = read.get('limit', 100)
                end = read.get('end')
                limit_max = config_snapshot['read_limit'] if kind == 'text' else 100
                if kind not in ('text', 'boundaries', 'headings', 'chunks') or type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= limit_max or (end is not None and (type(end) is not int or end < start)):
                    raise ValueError()
                page = self.reader(start, end, limit, kind)
                rendered = json.dumps(page, ensure_ascii=False)
                if any(secret in rendered for secret in secrets):
                    raise ValueError()
                messages.extend([{'role': 'assistant', 'content': content}, {'role': 'user', 'content': rendered}])
            except (KeyError, IndexError, TypeError, ValueError, CliError):
                raise _invalid() from None
        raise _invalid()
