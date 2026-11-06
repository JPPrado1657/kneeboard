"""Update a source-installed Kneeboard without discarding local work."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

REMOTES = {'https://github.com/JPPrado1657/kneeboard',
           'git@github.com:JPPrado1657/kneeboard',
           'ssh://git@github.com/JPPrado1657/kneeboard'}


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300).stdout.strip()


def update(repo, python, skills_dir):
    repo = Path(repo).resolve()
    python = Path(python).absolute()
    skills_dir = Path(skills_dir).expanduser().resolve()
    if not python.is_file():
        raise ValueError('The selected Python executable does not exist')
    root = Path(run('git', 'rev-parse', '--show-toplevel', cwd=repo)).resolve()
    if root != repo:
        raise ValueError('--repo must be the Kneeboard checkout root')
    if run('git', 'status', '--porcelain', '--untracked-files=all', cwd=repo):
        raise ValueError('Checkout has local changes; commit or move them before updating. Nothing was changed.')
    branch = run('git', 'symbolic-ref', '--short', 'HEAD', cwd=repo)
    if branch != 'main':
        raise ValueError('Updates require the main branch; no branch was switched')
    remote = run('git', 'remote', 'get-url', 'origin', cwd=repo).removesuffix('.git').rstrip('/')
    if remote not in REMOTES:
        raise ValueError('origin is not the official JPPrado1657/kneeboard repository')
    # Inspect the selected environment rather than accidentally using the agent's Python.
    run(str(python), '-c', 'import importlib.metadata; print(importlib.metadata.version("ai-kneeboard"))')
    run(str(python), '-m', 'pip', '--version')
    before = run('git', 'rev-parse', 'HEAD', cwd=repo)
    run('git', 'fetch', 'origin', 'refs/heads/main', cwd=repo)
    target = run('git', 'rev-parse', 'FETCH_HEAD', cwd=repo)
    # Refuse local-only commits as well as divergent histories; never reset or stash.
    run('git', 'merge-base', '--is-ancestor', before, target, cwd=repo)
    run('git', 'merge', '--ff-only', target, cwd=repo)
    print('Source commit: ' + before + ' -> ' + target, flush=True)
    print('Installing into: ' + str(python), flush=True)
    run(str(python), '-m', 'pip', 'install', '--upgrade', str(repo))
    run(str(python), '-m', 'kneeboard.app', '--help')
    for name in ('kneeboard', 'kneeboard-update'):
        source = repo / 'skills' / name
        destination = skills_dir / name
        if not (source / 'SKILL.md').is_file():
            raise ValueError('Missing bundled skill: ' + name)
        if source.resolve() == destination.resolve():
            continue
        # Never follow a destination skill symlink outside the chosen skills directory.
        if destination.is_symlink() or any(p.is_symlink() for p in destination.rglob('*')):
            raise ValueError('Skill destination contains symlinks: ' + str(destination))
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    print('Updated application and both skills. Restart Kneeboard and start a new AI session.')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='Existing official Kneeboard checkout')
    parser.add_argument('--python', required=True, help='Python executable in the installed Kneeboard environment')
    parser.add_argument('--skills-dir', default=str(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills'))
    args = parser.parse_args()
    try:
        update(args.repo, args.python, args.skills_dir)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print('Update stopped: ' + (detail or str(exc)), file=sys.stderr)
        print('Earlier steps may have succeeded. No automatic rollback was attempted; fix the error and rerun.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
