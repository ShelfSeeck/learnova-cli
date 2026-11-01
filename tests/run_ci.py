"""Run the same acceptance commands on native CI runners."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check_entry(command, env, cwd):
    success = subprocess.run(command + ['config', '--json'], cwd=cwd, env=env,
                             capture_output=True, text=True, encoding='utf-8', timeout=30)
    if success.returncode or not isinstance(json.loads(success.stdout), dict):
        raise RuntimeError('entry point failed to return configuration JSON')
    failure = subprocess.run(command + ['unknown-command', '--json'], cwd=cwd, env=env,
                             capture_output=True, text=True, encoding='utf-8', timeout=30)
    if failure.returncode != 2 or json.loads(failure.stdout).get('error', {}).get('code') != 'BAD_USAGE':
        raise RuntimeError('entry point did not preserve the usage error exit code')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-platform', choices=('Linux', 'Darwin', 'Windows'))
    parser.add_argument('--require-powershell', action='store_true')
    args = parser.parse_args()
    if args.expected_platform and platform.system() != args.expected_platform:
        parser.error('runner operating system does not match the requested acceptance platform')
    # Keep fixture reads, child processes and Unicode CLI output deterministic
    # when invoked locally from a Windows terminal using a legacy code page.
    os.environ['PYTHONUTF8'] = '1'
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', 'tests', '-v'], cwd=ROOT, check=True)
    subprocess.run([sys.executable, 'tests/t1_mirror_flow.py'], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory(prefix='learnova-entry-') as temp:
        env = {**os.environ, 'LEARNOVA_HOME': str(Path(temp) / 'learnova'),
               'LEARNOVA_TELEMETRY': '0', 'PYTHONIOENCODING': 'utf-8',
               'XDG_CACHE_HOME': str(Path(temp) / 'cache'),
               'XDG_CONFIG_HOME': str(Path(temp) / 'config'),
               'XDG_DATA_HOME': str(Path(temp) / 'data'), 'POWERSHELL_TELEMETRY_OPTOUT': '1'}
        # Make each thin entry select the interpreter used by this matrix job.
        executable = str(Path(sys.executable).resolve().parent)
        env['PATH'] = executable + os.pathsep + env.get('PATH', '')
        package = ROOT / 'skills' / 'learnova-cli'
        check_entry([sys.executable, str(package / 'scripts/learnova.py')], env, temp)
        if os.name != 'nt':
            check_entry(['sh', str(package / 'learnova-cli')], env, temp)
        pwsh = shutil.which('pwsh', path=env['PATH'])
        if pwsh:
            check_entry([pwsh, '-NoLogo', '-NoProfile', '-File', str(package / 'learnova-cli.ps1')], env, temp)
        elif args.require_powershell:
            raise RuntimeError('PowerShell is required for this acceptance job')
    print('CI acceptance passed on ' + platform.system() + ' / Python ' + platform.python_version())


if __name__ == '__main__':
    main()
