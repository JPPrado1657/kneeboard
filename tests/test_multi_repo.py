import json
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from kneeboard.app import Handler, LocalServer, Session


class MultiRepositoryTests(unittest.TestCase):
    def setUp(self):
        state_temp = tempfile.TemporaryDirectory()
        self.addCleanup(state_temp.cleanup)
        state_env = patch.dict('os.environ', {'KNEEBOARD_STATE_DIR': state_temp.name})
        state_env.start()
        self.addCleanup(state_env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Identical names, paths within each repo, and content must still be isolated.
        self.paths = [self.root / 'one' / 'service', self.root / 'two' / 'service']
        for path in self.paths:
            path.mkdir(parents=True)
            self.git(path, 'init')
            self.git(path, 'config', 'user.name', 'Test')
            self.git(path, 'config', 'user.email', 'test@example.com')
            (path / 'shared.txt').write_text('before\n')
            self.git(path, 'add', '.')
            self.git(path, 'commit', '-m', 'initial')
            (path / 'shared.txt').write_text('after\n')
        self.session = Session(self.paths, 'HEAD')

    def git(self, path, *args):
        subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True)

    def report(self, repo, title='Review'):
        return dict(repositoryId=repo['id'], snapshotId=repo['snapshot']['id'],
                    title=title, summary='Test review', findings=[dict(
                        priority='P2', title='Finding', detail='Test finding',
                        evidence=[dict(path='shared.txt', side='new', line=1, quote='after')])])

    def start_server(self):
        server = LocalServer(('127.0.0.1', 0), Handler)
        server.session = self.session
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.addCleanup(stop)
        return f'http://127.0.0.1:{server.server_port}/{self.session.token}/'

    def request(self, url, route='state', payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        with urllib.request.urlopen(urllib.request.Request(url + route, data=data), timeout=5) as response:
            return json.load(response)

    def test_same_named_repositories_have_distinct_identity_and_evidence_scope(self):
        first, second = self.session.repositories
        self.assertEqual(first['name'], second['name'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['snapshot']['id'], second['snapshot']['id'])
        wrong_target = self.report(first)
        wrong_target['repositoryId'] = second['id']
        with self.assertRaisesRegex(ValueError, 'snapshotId'):
            self.session.submit(wrong_target)
        self.assertIsNone(second['report'])

    def test_duplicate_repository_roots_are_rejected(self):
        subdir = self.paths[0] / 'subdir'
        subdir.mkdir()
        with self.assertRaisesRegex(ValueError, 'more than once'):
            Session([self.paths[0], subdir], 'HEAD')

    def test_http_isolation_refresh_and_explicit_target(self):
        url = self.start_server()
        state = self.request(url)
        self.assertEqual(len(state['repositories']), 2)
        self.assertNotIn('snapshot', state)
        first, second = state['repositories']
        for repo in (first, second):
            self.assertEqual(self.request(url, 'report', self.report(repo, repo['path'])), {'ok': True})
        for route, payload in [('refresh', {}), ('refresh', {'repositoryId': 'unknown'}),
                               ('report', dict(self.report(first), repositoryId=None))]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(url, route, payload)
            self.assertEqual(error.exception.code, 400)
        (self.paths[0] / 'shared.txt').write_text('changed again\n')
        # An edit to repo A must not invalidate repo B's otherwise valid review.
        self.request(url, 'report', self.report(second, 'Second review'))
        with self.assertRaises(urllib.error.HTTPError):
            self.request(url, 'report', self.report(first))
        self.request(url, 'refresh', {'repositoryId': first['id']})
        updated = self.request(url)['repositories']
        self.assertIsNone(updated[0]['report'])
        self.assertNotEqual(updated[0]['snapshot']['id'], first['snapshot']['id'])
        self.assertEqual(updated[1]['report']['title'], 'Second review')
        self.assertEqual(updated[1]['snapshot'], second['snapshot'])

    def test_serve_cli_accepts_multiple_repositories_and_closes_cleanly(self):
        process = subprocess.Popen([sys.executable, '-u', '-m', 'kneeboard.app', 'serve',
                                    *map(str, self.paths), '--no-open'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        try:
            url = lines.get(timeout=10).strip()
            self.assertTrue(url.startswith('http://127.0.0.1:'), url)
            state = self.request(url)
            self.assertEqual(len(state['repositories']), 2)
            self.assertEqual(set(state['overview']['snapshotIds']), {repo['id'] for repo in state['repositories']})
            self.request(url, 'overview', self.overview())
            self.request(url, 'close', {})
            self.assertEqual(process.wait(timeout=5), 0)
            reopened = subprocess.Popen([sys.executable, '-u', '-m', 'kneeboard.app', 'serve',
                                         *map(str, self.paths), '--no-open'],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                restarted_url = reopened.stdout.readline().strip()
                restored = self.request(restarted_url)
                self.assertEqual(restored['overview']['report']['title'], 'System review')
                self.assertEqual(restored['repositories'], state['repositories'])
                self.request(restarted_url, 'close', {})
                self.assertEqual(reopened.wait(timeout=5), 0)
            finally:
                if reopened.poll() is None:
                    reopened.terminate()
                    reopened.wait(timeout=5)
                reopened.stdout.close()
                reopened.stderr.close()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

    def test_submit_cli_targets_selected_repository(self):
        url = self.start_server()
        first, second = self.session.repositories
        report = self.report(second, 'CLI review')
        report.pop('repositoryId')
        result = subprocess.run([sys.executable, '-m', 'kneeboard.app', 'submit', url, '--repo', second['id']],
                                input=json.dumps(report), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(first['report'])
        self.assertEqual(second['report']['title'], 'CLI review')
        result = subprocess.run([sys.executable, '-m', 'kneeboard.app', 'submit', url, '--repo', first['id']],
                                input=json.dumps(self.report(second)), text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('conflicts', result.stderr)
        self.assertIsNone(first['report'])

    def overview(self):
        first, second = self.session.repositories
        evidence = [dict(repositoryId=repo['id'], path='shared.txt', side='new', line=1, quote='after')
                    for repo in (first, second)]
        return dict(snapshotIds=self.session.snapshot_ids(), title='System review', summary='Two services change together',
                    findings=[dict(priority='P1', title='Contract mismatch', detail='Check both ends', evidence=evidence)],
                    behaviors=[dict(title='Request flow', detail='The caller and service now use the same contract', evidence=evidence)],
                    diagrams=[dict(title='Request flow', nodes=['Web', 'API'], edges=[['Web', 'API']]),
                              dict(title='Return path', nodes=['API', 'Web'], edges=[['API', 'Web']])])

    def test_system_overview_stores_cross_repository_content_independently(self):
        url = self.start_server()
        report = self.overview()
        self.request(url, 'overview', report)
        state = self.request(url)
        self.assertEqual(state['overview']['report'], report)
        self.assertTrue(all(repo['report'] is None for repo in state['repositories']))
        self.session.submit(self.report(self.session.repositories[0]))
        self.assertEqual(self.request(url)['overview']['report'], report)

    def test_system_overview_rejects_incomplete_scope_and_bad_cross_repo_evidence(self):
        for change in ('missing_snapshot', 'unknown_repo', 'missing_repo', 'bad_quote'):
            report = self.overview()
            if change == 'missing_snapshot':
                report['snapshotIds'].pop(self.session.repositories[1]['id'])
            elif change == 'unknown_repo':
                report['behaviors'][0]['evidence'][1]['repositoryId'] = 'unknown'
            elif change == 'missing_repo':
                report['findings'][0]['evidence'][0].pop('repositoryId')
            else:
                report['behaviors'][0]['evidence'][1]['quote'] = 'invented'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.session.submit_overview(report)
            self.assertIsNone(self.session.overview_report)
        (self.paths[1] / 'shared.txt').write_text('new contract\n')
        with self.assertRaisesRegex(ValueError, 'Repository changed'):
            self.session.submit_overview(self.overview())

    def test_refresh_invalidates_system_overview_but_preserves_other_repo_review(self):
        first, second = self.session.repositories
        self.session.submit(self.report(second))
        self.session.submit_overview(self.overview())
        self.session.refresh(first['id'])
        self.assertIsNone(self.session.overview_report)
        self.assertIsNotNone(second['report'])
        self.session.submit_overview(self.overview())
        url = self.start_server()
        (self.paths[1] / 'shared.txt').write_text('another change\n')
        before = self.session.snapshot_ids()
        self.request(url, 'refresh-all', {})
        state = self.request(url)
        self.assertIsNone(state['overview']['report'])
        self.assertTrue(all(repo['report'] is None for repo in state['repositories']))
        self.assertNotEqual(before[second['id']], state['overview']['snapshotIds'][second['id']])

    def test_diagram_sources_survive_submission_and_viewer_has_separate_csp(self):
        url = self.start_server()
        report = self.overview()
        report['diagrams'] = [dict(title='Contract flow', format='mermaid', source='sequenceDiagram\nWeb->>API: Request')]
        self.request(url, 'overview', report)
        self.assertEqual(self.request(url)['overview']['report']['diagrams'], report['diagrams'])
        with urllib.request.urlopen(url + 'diagram.html', timeout=5) as response:
            self.assertIn("connect-src 'none'", response.headers['Content-Security-Policy'])
            self.assertIn("frame-ancestors 'self'", response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with urllib.request.urlopen(url, timeout=5) as response:
            self.assertNotIn("'unsafe-inline'", response.headers['Content-Security-Policy'])
            self.assertIn("frame-src 'self'", response.headers['Content-Security-Policy'])

    def test_overview_cli_submits_system_content(self):
        url = self.start_server()
        report = self.overview()
        result = subprocess.run([sys.executable, '-m', 'kneeboard.app', 'submit', url, '--overview'],
                                input=json.dumps(report), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.session.overview_report, report)

    def test_temporary_tab_disconnect_and_reload_preserve_reports(self):
        self.session.submit_overview(self.overview())
        with patch('kneeboard.app.time.monotonic', return_value=100):
            self.session.heartbeat('user-tab')
            self.session.heartbeat('agent-inspection-tab')
            self.session.disconnect('agent-inspection-tab')
        self.assertFalse(self.session.expired(120, now=110))
        with patch('kneeboard.app.time.monotonic', return_value=110):
            self.session.disconnect('user-tab')
        self.assertFalse(self.session.expired(120, now=139))
        with patch('kneeboard.app.time.monotonic', return_value=135):
            self.session.heartbeat('reloaded-tab')
        self.assertFalse(self.session.expired(120, now=141))
        self.assertIsNotNone(self.session.overview_report)
        self.assertTrue(self.session.expired(120, now=256))

    def test_review_lease_protects_background_analysis_and_eventually_expires(self):
        with patch('kneeboard.app.time.monotonic', return_value=100):
            self.session.last_seen = 100
            self.session.heartbeat('tab')
            lease = self.session.start_review()
            self.session.disconnect('tab')
        self.assertFalse(self.session.expired(120, now=230))
        with patch('kneeboard.app.time.monotonic', return_value=1000):
            self.assertEqual(self.session.start_review(lease), lease)
        self.assertFalse(self.session.expired(120, now=2000))
        self.assertTrue(self.session.expired(120, now=2801))
        self.assertEqual(self.session.review_leases, {})

    def test_finished_review_allows_background_browser_to_resume(self):
        with patch('kneeboard.app.time.monotonic', return_value=100):
            self.session.heartbeat('background-tab')
            lease = self.session.start_review()
        self.assertFalse(self.session.expired(120, now=300))
        with patch('kneeboard.app.time.monotonic', return_value=310):
            self.session.finish_review(lease)
        self.assertFalse(self.session.expired(120, now=320))
        with patch('kneeboard.app.time.monotonic', return_value=325):
            self.session.heartbeat('background-tab')
        self.assertFalse(self.session.expired(120, now=440))

    def test_agent_connect_enrich_verify_and_finish_leave_user_session_open(self):
        url = self.start_server()
        self.request(url, 'heartbeat', {'clientId':'user-tab'})
        command = [sys.executable, '-m', 'kneeboard.app']
        def cli(*args, payload=None):
            result = subprocess.run(command + list(args), input=None if payload is None else json.dumps(payload),
                                    text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        connection = cli('connect', url)
        lease = connection['leaseId']
        self.assertEqual(len(connection['state']['repositories']), 2)
        self.assertEqual(connection['state']['connection']['activeReviews'], 1)
        for repo in connection['state']['repositories']:
            cli('submit', url, '--repo', repo['id'], payload=self.report(repo, 'Delivered'))
        cli('submit', url, '--overview', payload=self.overview())
        verified = cli('connect', url, '--lease', lease)['state']
        self.assertTrue(all(repo['report']['title'] == 'Delivered' for repo in verified['repositories']))
        self.assertEqual(verified['overview']['report']['title'], 'System review')
        cli('finish', url, '--lease', lease)
        self.assertEqual(self.request(url)['connection']['activeReviews'], 0)
        self.assertIsNotNone(self.request(url)['overview']['report'])
        self.assertFalse(self.session.expired(120))

    def test_submit_cli_explains_validation_errors_without_closing_session(self):
        url = self.start_server()
        report = self.report(self.session.repositories[0])
        report['findings'][0]['evidence'][0]['quote'] = 'wrong quote'
        result = subprocess.run([sys.executable, '-m', 'kneeboard.app', 'submit', url],
                                input=json.dumps(report), text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exact quote do not match', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(len(self.request(url)['repositories']), 2)

    def test_saved_reviews_survive_shutdown_edits_and_reordered_repositories(self):
        for repo in self.session.repositories:
            self.session.submit(self.report(repo))
        overview = self.overview()
        overview['diagrams'] = [dict(title='Flow', format='mermaid', source='graph LR; A-->B')]
        self.session.submit_overview(overview)
        expected = json.loads(json.dumps(self.session.state()))
        token = self.session.token
        self.session.clear()  # Server shutdown releases only the in-memory copy.
        (self.paths[0] / 'shared.txt').write_text('interface changed\n')
        restored = Session(list(reversed(self.paths)), 'HEAD')
        self.assertEqual(restored.overview_report, expected['overview']['report'])
        self.assertEqual(restored.repositories, list(reversed(expected['repositories'])))
        self.assertNotEqual(restored.token, token)
        self.assertEqual(restored.clients, {})
        self.assertEqual(restored.review_leases, {})
        with self.assertRaisesRegex(ValueError, 'Repository changed'):
            restored.submit(restored.repositories[1]['report'])
        restored.refresh_all()
        reopened = Session(self.paths, 'HEAD')
        self.assertIsNone(reopened.overview_report)
        self.assertTrue(all(repo['report'] is None for repo in reopened.repositories))
        self.assertIn('interface changed', reopened.repositories[0]['snapshot']['files'][0]['new'])

    def test_refresh_one_is_persisted_without_erasing_other_review(self):
        first, second = self.session.repositories
        for repo in (first, second):
            self.session.submit(self.report(repo))
        self.session.submit_overview(self.overview())
        self.session.refresh(first['id'])
        restored = Session(self.paths, 'HEAD')
        self.assertIsNone(restored.overview_report)
        self.assertIsNone(restored.repositories[0]['report'])
        self.assertEqual(restored.repositories[1]['report'], second['report'])
        self.assertEqual(self.session.state_path.stat().st_mode & 0o777, 0o600)
        saved = json.loads(self.session.state_path.read_text())
        self.assertNotIn('token', saved)
        self.assertNotIn('review_leases', saved)
        self.assertIsNone(Session([self.paths[0]], 'HEAD').report)
        self.assertIsNone(Session(self.paths, 'HEAD~0').report)

    def test_corrupt_save_is_reported_and_preserved(self):
        self.session.state_path.write_text('{broken')
        with self.assertRaisesRegex(ValueError, 'Could not restore saved session'):
            Session(self.paths, 'HEAD')
        self.assertEqual(self.session.state_path.read_text(), '{broken')

    def test_clear_releases_all_repository_review_data(self):
        references = list(self.session.repositories)
        for repo in references:
            self.session.submit(self.report(repo))
        self.session.submit_overview(self.overview())
        self.session.clear()
        self.assertIsNone(self.session.overview_report)
        self.assertEqual(self.session.repositories, [])
        for repo in references:
            self.assertEqual(repo['snapshot'], {})
            self.assertIsNone(repo['report'])


if __name__ == '__main__':
    unittest.main()
