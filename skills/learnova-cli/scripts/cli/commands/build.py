"""Local document build commands; implementation loads only when invoked."""
import json
from pathlib import Path
from core.errors import CliError


def register(sub):
    p = sub.add_parser('build', help='构建知识参考型 Skill')
    actions = p.add_subparsers(dest='build_action', required=True)
    create = actions.add_parser('create')
    create.add_argument('input')
    create.add_argument('--name')
    create.add_argument('--output')
    create.add_argument('--mode', choices=('agent', 'auto'), default='agent')
    create.add_argument('--parser', choices=('remote', 'local'))
    create.add_argument('--force', action='store_true')
    parsers = [create]
    for action in ('next', 'status', 'pause', 'package', 'read', 'submit', 'resume', 'run', 'reconfigure'):
        item = actions.add_parser(action)
        item.add_argument('task_id')
        parsers.append(item)
        if action == 'read':
            item.add_argument('--start', type=int, default=0)
            item.add_argument('--end', type=int)
            item.add_argument('--limit', type=int)
            item.add_argument('--kind', choices=('text', 'boundaries', 'headings', 'chunks'), default='text')
        if action == 'reconfigure':
            item.add_argument('--set', dest='settings', action='append', required=True, metavar='KEY=VALUE')
        if action == 'submit':
            group = item.add_mutually_exclusive_group(required=True)
            group.add_argument('--file')
            group.add_argument('--data')
        if action in ('resume', 'run'):
            item.add_argument('--mode', choices=('agent', 'auto'))
        if action == 'resume':
            item.add_argument('--force', action='store_true')
            item.add_argument('--from', dest='from_stage', choices=('parsing', 'chunking', 'indexing', 'finalize'))
        if action == 'run':
            item.add_argument('--until', choices=('parsing', 'chunking', 'indexing', 'finalize'))
            item.add_argument('--max-steps', type=int, default=100)
    cfg = actions.add_parser('config')
    cfg.add_argument('config_action', choices=('get', 'set'))
    cfg.add_argument('key', nargs='?')
    cfg.add_argument('value', nargs='?')
    cfg.add_argument('--user', action='store_true')
    parsers.append(cfg)
    for item in parsers:
        item.add_argument('--project-root', default='.')
        item.set_defaults(handler_fn=handler)


def handler(args, ctx):
    from build.pipeline import BuildService
    from build import config
    root = Path(args.project_root).resolve()
    if args.build_action == 'config':
        if args.config_action == 'get':
            return config.load(root)
        if not args.key or args.value is None:
            raise CliError('BAD_USAGE', '需要配置键和值', 2)
        return config.set_value(root, args.key, args.value, args.user)
    from build.parsers import Parser
    service = BuildService(root, parser=Parser())
    if args.build_action == 'run':
        from build.decisions import Decision
        service.decision = Decision(lambda start, end, limit, kind: service.read(args.task_id, start, end, limit, kind))
    action = args.build_action
    if action == 'create':
        return service.create(args.input, args.name, args.output, args.mode, args.parser, args.force)
    if action == 'read':
        return service.read(args.task_id, args.start, args.end, args.limit, args.kind)
    if action == 'reconfigure':
        values = {}
        for setting in args.settings:
            key, separator, value = setting.partition('=')
            if not separator or not key or key in values:
                raise CliError('BAD_USAGE', '配置需使用不重复的 KEY=VALUE', 2)
            values[key] = value
        return service.reconfigure(args.task_id, values)
    if action == 'submit':
        try:
            if args.file:
                return service.submit_file(args.task_id, args.file)
            result = json.loads(args.data)
        except (OSError, ValueError) as exc:
            raise CliError('BAD_USAGE', '无法读取 JSON 结果', 2) from exc
        return service.submit(args.task_id, result)
    if action == 'resume':
        return service.resume(args.task_id, args.from_stage, args.mode, args.force)
    if action == 'run':
        if args.max_steps < 1:
            raise CliError('BAD_USAGE', 'max-steps 必须为正整数', 2)
        return service.run(args.task_id, args.mode, args.until, args.max_steps)
    return getattr(service, action)(args.task_id)
