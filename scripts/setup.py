"""Install Kneeboard and its AI skills from this checkout."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def environment_python(folder):
    return folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def check_skill_destinations(repo, skills_dir):
    for name in ('kneeboard', 'kneeboard-update'):
        source = repo / 'skills' / name
        destination = skills_dir / name
        if not (source / 'SKILL.md').is_file():
            raise ValueError('Missing bundled skill: ' + str(source))
        if source.resolve() == destination.resolve():
            continue
        if destination.is_symlink() or any(p.is_symlink() for p in destination.rglob('*')):
            raise ValueError('Skill destination contains symlinks: ' + str(destination))
        if destination.exists() and not destination.is_dir():
            raise ValueError('Skill destination is not a directory: ' + str(destination))


def setup(repo, environment, skills_dir=None):
    repo = Path(repo).resolve()
    # Do not resolve the environment's Python symlink: pip must run inside the venv.
    environment = Path(environment).expanduser().absolute()
    if sys.version_info < (3, 10):
        raise ValueError('Python 3.10 or newer is required')
    if not shutil.which('git'):
        raise ValueError('Install Git and rerun setup')
    if not (repo / 'pyproject.toml').is_file() or not (repo / 'kneeboard/app.py').is_file():
        raise ValueError('Run the setup script from a complete Kneeboard checkout')
    if environment.is_symlink():
        raise ValueError('The environment directory must not be a symlink')
    if environment.exists() and (not environment.is_dir() or
                                 (any(environment.iterdir()) and not (environment / 'pyvenv.cfg').is_file())):
        raise ValueError('The environment path is occupied by something other than a virtual environment')
    if skills_dir is not None:
        skills_dir = Path(skills_dir).expanduser().resolve()
        check_skill_destinations(repo, skills_dir)
    python = environment_python(environment)
    if not (environment / 'pyvenv.cfg').is_file():
        print('Creating Python environment: ' + str(environment), flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    if not python.is_file():
        raise ValueError('Existing environment has no usable Python; choose another --venv path')
    print('Installing Kneeboard into: ' + str(environment), flush=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', str(repo)], check=True)
    subprocess.run([str(python), '-m', 'kneeboard.app', '--help'], check=True, stdout=subprocess.DEVNULL)
    if skills_dir is not None:
        for name in ('kneeboard', 'kneeboard-update'):
            source, destination = repo / 'skills' / name, skills_dir / name
            if source.resolve() != destination.resolve():
                shutil.copytree(source, destination, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print('Installed both skills in: ' + str(skills_dir), flush=True)
    print('\nSetup complete. Launch a review with:', flush=True)
    if os.name == 'nt':
        command = "& '" + str(python).replace("'", "''") + "' -m kneeboard.app serve /path/to/your/repository"
    else:
        import shlex
        command = shlex.join([str(python), '-m', 'kneeboard.app', 'serve', '/path/to/your/repository'])
    print('  ' + command)
    if skills_dir is not None:
        print('Start a new AI session to load the skills. Ask it to use kneeboard or kneeboard-update.')
    return python


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv', type=Path, default=repo / '.venv', help='Environment location (default: checkout/.venv)')
    parser.add_argument('--skills-dir', type=Path,
                        default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills',
                        help='Skill destination (default: $CODEX_HOME/skills or ~/.codex/skills)')
    parser.add_argument('--skip-skills', action='store_true', help='Install only the application')
    args = parser.parse_args()
    try:
        setup(repo, args.venv, None if args.skip_skills else args.skills_dir)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Setup stopped: ' + str(exc), file=sys.stderr)
        print('Completed steps are kept. Resolve the error and rerun; setup reuses its virtual environment.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
