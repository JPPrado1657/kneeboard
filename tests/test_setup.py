import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('installer', Path(__file__).parents[1] / 'scripts/setup.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = Path(__file__).parents[1]

    def test_occupied_environment_is_not_overwritten(self):
        environment = self.root / 'env'
        environment.mkdir()
        personal = environment / 'personal.txt'
        personal.write_text('keep this')
        with self.assertRaisesRegex(ValueError, 'occupied'):
            installer.setup(self.repo, environment)
        self.assertEqual(personal.read_text(), 'keep this')

    def test_symlinked_skill_is_not_overwritten(self):
        skills = self.root / 'skills'
        skills.mkdir()
        target = self.root / 'personal-skill'
        target.mkdir()
        file = target / 'SKILL.md'
        file.write_text('keep this')
        (skills / 'kneeboard').symlink_to(target, target_is_directory=True)
        environment = self.root / 'env'
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            installer.setup(self.repo, environment, skills)
        self.assertFalse(environment.exists())
        self.assertEqual(file.read_text(), 'keep this')

    def test_symlinked_environment_is_not_reused(self):
        target = self.root / 'personal'
        target.mkdir()
        environment = self.root / 'env'
        environment.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            installer.setup(self.repo, environment)
        self.assertEqual(list(target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
