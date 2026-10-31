"""Local document build commands; implementation loads only when invoked."""
import json
from pathlib import Path
from core.errors import CliError


def register(sub):
    p = sub.add_parser('build', help='构建知识参考型 Skill')
    actions = p.add_subparsers(dest='build_action', required=True)
    parsers = []
    tasks = actions.add_parser('tasks')
    tasks.add_argument('--limit', type=int, default=20)
    parsers.append(tasks)
    for action in ('create', 'start', 'doctor'):
        item = actions.add_parser(action)
        item.add_argument('input')
        item.add_argument('--mode', choices=('agent', 'auto'), default='agent')
        item.add_argument('--parser', choices=('remote', 'local'))
        if action != 'doctor':
            item.add_argument('--name')
            item.add_argument('--output')
            item.add_argument('--force', action='store_true')
        if action == 'start':
            item.add_argument('--preset', choices=('mineru',))
        parsers.append(item)
    for action in ('next', 'status', 'pause', 'package', 'read', 'submit', 'resume', 'run', 'work', 'reconfigure'):
        item = actions.add_parser(action)
        item.add_argument('task_id')
        parsers.append(item)
        if action == 'read':
            item.add_argument('--start', type=int, default=0)
            item.add_argument('--end', type=int)
            item.add_argument('--limit', type=int)
            item.add_argument('--kind', choices=('text', 'boundaries', 'headings', 'chunks'), default='text')
        if action == 'reconfigure':
            item.add_argument('--set', dest='settings', action='append', default=[], metavar='KEY=VALUE')
            item.add_argument('--preset', choices=('mineru',))
        if action == 'submit':
            group = item.add_mutually_exclusive_group(required=True)
            group.add_argument('--file')
            group.add_argument('--data')
            item.add_argument('--continue', dest='continue_build', action='store_true')
        if action in ('work', 'submit'):
            item.add_argument('--batch-size', type=int, choices=range(1, 17), default=8)
            item.add_argument('--max-steps', type=int, default=1000)
        if action in ('resume', 'run'):
            item.add_argument('--mode', choices=('agent', 'auto'))
        if action == 'resume':
            item.add_argument('--force', action='store_true')
            item.add_argument('--from', dest='from_stage', choices=('parsing', 'chunking', 'indexing', 'finalize'))
        if action == 'run':
            item.add_argument('--until', choices=('parsing', 'chunking', 'indexing', 'finalize'))
            item.add_argument('--max-steps', type=int, default=100)
    cfg = actions.add_parser('config')
    cfg.add_argument('config_action', choices=('get', 'set', 'preset'))
    cfg.add_argument('key', nargs='?')
    cfg.add_argument('value', nargs='?')
    cfg.add_argument('--user', action='store_true')
    cfg.add_argument('--set', dest='settings', action='append', default=[], metavar='KEY=VALUE')
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
        if args.config_action == 'preset':
            return {'preset': args.key, **config.set_values(root, config.preset_values(args.key), args.user)}
        if args.settings:
            if args.key or args.value is not None:
                raise CliError('BAD_USAGE', '批量配置不能混用位置参数', 2)
            values = {}
            for setting in args.settings:
                key, separator, value = setting.partition('=')
                if not separator or not key or key in values:
                    raise CliError('BAD_USAGE', '配置需使用不重复的 KEY=VALUE', 2)
                values[key] = value
            return config.set_values(root, values, args.user)
        if not args.key or args.value is None:
            raise CliError('BAD_USAGE', '需要配置键和值', 2)
        return config.set_value(root, args.key, args.value, args.user)
    from build.parsers import Parser
    service = BuildService(root, parser=Parser())
    if args.build_action in ('run', 'work', 'submit'):
        from build.decisions import Decision
        service.decision = Decision(lambda start, end, limit, kind: service.read(args.task_id, start, end, limit, kind))
    action = args.build_action
    if action == 'tasks':
        return service.tasks(args.limit)
    if action == 'doctor':
        from build.preflight import inspect
        return inspect(root, args.input, args.mode, args.parser)
    if action == 'start':
        return service.start(args.input, args.name, args.output, args.mode, args.parser, args.force, args.preset)
    if action == 'create':
        return service.create(args.input, args.name, args.output, args.mode, args.parser, args.force)
    if action == 'read':
        return service.read(args.task_id, args.start, args.end, args.limit, args.kind)
    if action == 'reconfigure':
        values = config.preset_values(args.preset) if args.preset else {}
        explicit = set()
        for setting in args.settings:
            key, separator, value = setting.partition('=')
            if not separator or not key or key in explicit:
                raise CliError('BAD_USAGE', '配置需使用不重复的 KEY=VALUE', 2)
            values[key] = value
            explicit.add(key)
        if not values:
            raise CliError('BAD_USAGE', '需要 --preset 或 --set', 2)
        return service.reconfigure(args.task_id, values)
    if action == 'submit':
        if args.max_steps < 1:
            raise CliError('BAD_USAGE', 'max-steps 必须为正整数', 2)
        try:
            if args.file:
                state = service.submit_file(args.task_id, args.file)
            else:
                state = service.submit(args.task_id, json.loads(args.data))
        except (OSError, ValueError) as exc:
            raise CliError('BAD_USAGE', '无法读取 JSON 结果', 2) from exc
        return service.work(args.task_id, args.batch_size, args.max_steps) if args.continue_build else state
    if action == 'resume':
        return service.resume(args.task_id, args.from_stage, args.mode, args.force)
    if action == 'run':
        if args.max_steps < 1:
            raise CliError('BAD_USAGE', 'max-steps 必须为正整数', 2)
        return service.run(args.task_id, args.mode, args.until, args.max_steps)
    if action == 'work':
        return service.work(args.task_id, args.batch_size, args.max_steps)
    return getattr(service, action)(args.task_id)
