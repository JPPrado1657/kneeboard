import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('updater', Path(__file__).parents[1] / 'skills/kneeboard-update/scripts/update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.repo = self.root / 'checkout'
        self.skills = self.root / 'installed-skills'
        self.git('init', '--bare', str(self.remote))
        self.git('init', '-b', 'main', str(self.repo))
        self.git('-C', str(self.repo), 'config', 'user.name', 'Test')
        self.git('-C', str(self.repo), 'config', 'user.email', 'test@example.com')
        for name in ('kneeboard', 'kneeboard-update'):
            folder = self.repo / 'skills' / name
            folder.mkdir(parents=True)
            (folder / 'SKILL.md').write_text('Original skill')
        self.commit('initial')
        self.git('-C', str(self.repo), 'remote', 'add', 'origin', str(self.remote))
        self.git('-C', str(self.repo), 'push', 'origin', 'main')
        self.real_run = updater.run
        self.python_calls = []
        self.addCleanup(patch.stopall)
        patch.object(updater, 'REMOTES', {str(self.remote).removesuffix('.git')}).start()
        patch.object(updater, 'run', side_effect=self.invoke).start()

    def git(self, *args):
        return subprocess.run(['git', *args], check=True, text=True, capture_output=True).stdout.strip()

    def commit(self, message):
        self.git('-C', str(self.repo), 'add', '.')
        self.git('-C', str(self.repo), 'commit', '-m', message)

    def invoke(self, *args, **kwargs):
        if args[0] == sys.executable:
            self.python_calls.append(args)
            return '0.1.0'
        return self.real_run(*args, **kwargs)

    def test_current_checkout_repairs_install_and_refreshes_both_skills(self):
        head = self.git('-C', str(self.repo), 'rev-parse', 'HEAD')
        self.assertEqual(updater.update(self.repo, sys.executable, self.skills), head)
        self.assertTrue(any(call[1:5] == ('-m', 'pip', 'install', '--upgrade') for call in self.python_calls))
        self.assertTrue((self.skills / 'kneeboard/SKILL.md').is_file())
        self.assertTrue((self.skills / 'kneeboard-update/SKILL.md').is_file())

    def test_dirty_checkout_is_preserved(self):
        file = self.repo / 'skills/kneeboard/SKILL.md'
        file.write_text('Local edits')
        with self.assertRaisesRegex(ValueError, 'local changes'):
            updater.update(self.repo, sys.executable, self.skills)
        self.assertEqual(file.read_text(), 'Local edits')
        self.assertFalse(self.python_calls)

    def test_local_commits_are_preserved(self):
        (self.repo / 'extra').write_text('local work')
        self.commit('local')
        head = self.git('-C', str(self.repo), 'rev-parse', 'HEAD')
        with self.assertRaises(subprocess.CalledProcessError):
            updater.update(self.repo, sys.executable, self.skills)
        self.assertEqual(self.git('-C', str(self.repo), 'rev-parse', 'HEAD'), head)
        self.assertFalse(self.skills.exists())

    def test_fast_forward_installs_fetched_commit(self):
        clone = self.root / 'author'
        self.git('clone', '-b', 'main', str(self.remote), str(clone))
        self.git('-C', str(clone), 'config', 'user.name', 'Test')
        self.git('-C', str(clone), 'config', 'user.email', 'test@example.com')
        (clone / 'skills/kneeboard/SKILL.md').write_text('Updated skill')
        self.git('-C', str(clone), 'commit', '-am', 'update')
        self.git('-C', str(clone), 'push', 'origin', 'main')
        target = self.git('-C', str(clone), 'rev-parse', 'HEAD')
        self.assertEqual(updater.update(self.repo, sys.executable, self.skills), target)
        self.assertEqual((self.skills / 'kneeboard/SKILL.md').read_text(), 'Updated skill')

    def test_install_failure_does_not_sync_skills(self):
        def fail_install(*args, **kwargs):
            if args[1:5] == ('-m', 'pip', 'install', '--upgrade'):
                raise subprocess.CalledProcessError(1, args, stderr='Install failed')
            return self.invoke(*args, **kwargs)
        with patch.object(updater, 'run', side_effect=fail_install):
            with self.assertRaises(subprocess.CalledProcessError):
                updater.update(self.repo, sys.executable, self.skills)
        self.assertFalse(self.skills.exists())


if __name__ == '__main__':
    unittest.main()
