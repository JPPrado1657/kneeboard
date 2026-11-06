import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from kneeboard.app import collect, validate_report, Session, Handler, LocalServer

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=Path(self.tmp.name)
        self.git('init');self.git('config','user.email','test@example.com');self.git('config','user.name','Test')
        (self.repo/'a.txt').write_text('before\n');self.git('add','.');self.git('commit','-m','initial')
        (self.repo/'a.txt').write_text('after\n')
    def tearDown(self):self.tmp.cleanup()
    def git(self,*args):subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True)
    def report(self,s):return dict(snapshotId=s['id'],title='Change',summary='Reviewed',findings=[dict(priority='P2',title='Issue',detail='Impact',evidence=[dict(path='a.txt',side='new',line=1,quote='after')])])
    def test_collect_staged_untracked_deleted_binary_and_symlink(self):
        self.git('add','a.txt');(self.repo/'extra.txt').write_text('new\n');(self.repo/'bin').write_bytes(b'\0abc');(self.repo/'link').symlink_to('/etc/passwd')
        files={f['path']:f for f in collect(self.repo)['files']}
        self.assertIn('+after',files['a.txt']['diff']);self.assertEqual(files['extra.txt']['status'],'untracked');self.assertTrue(files['bin']['omitted']);self.assertEqual(files['link']['new'],'')
        (self.repo/'a.txt').unlink();self.assertEqual(next(f for f in collect(self.repo)['files'] if f['path']=='a.txt')['status'],'deleted')
    def test_evidence_and_stale_id(self):
        s=collect(self.repo);r=self.report(s);validate_report(r,s)
        r['findings'][0]['evidence'][0]['quote']='fabricated'
        with self.assertRaises(ValueError):validate_report(r,s)
        r=self.report(s);r['snapshotId']='stale'
        with self.assertRaises(ValueError):validate_report(r,s)
    def test_http_submission_and_repository_mutation(self):
        session=Session(self.repo,'HEAD');server=LocalServer(('127.0.0.1',0),Handler);server.session=session
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}/{session.token}/'
        def post(route,data):return urllib.request.urlopen(urllib.request.Request(url+route,data=json.dumps(data).encode()), timeout=5)
        try:
            with post('report',self.report(session.snapshot)) as r:self.assertEqual(r.status,200)
            (self.repo/'a.txt').write_text('changed again\n')
            with self.assertRaises(urllib.error.HTTPError) as err:post('report',self.report(session.snapshot))
            self.assertEqual(err.exception.code,400)
            with post('refresh',{}) as r:self.assertEqual(r.status,200)
            self.assertIsNone(session.report)
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(url.replace(session.token,'wrong'), timeout=5)
            with post('close',{}) as r:self.assertEqual(r.status,200)
            thread.join(3);self.assertFalse(thread.is_alive())
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
