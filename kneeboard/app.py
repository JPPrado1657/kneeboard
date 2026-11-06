import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

class LocalServer(ThreadingHTTPServer):
    def server_bind(self):
        # A loopback-only server does not need reverse DNS (which can block).
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


LIMIT = 1_000_000

def git(repo, *args):
    p = subprocess.run(['git', '-C', str(repo), *args], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=30)
    if p.returncode:
        raise ValueError(p.stderr.decode(errors='replace').strip())
    return p.stdout

def collect(repo, base='HEAD'):
    root = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    # Resolve once and only pass a verified object ID to diff/show.
    revision = git(root, 'rev-parse', '--verify', base + '^{commit}').decode().strip()
    changed = git(root, 'diff', '--name-only', '-z', revision, '--').split(b'\0')
    untracked = git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    files = []
    for raw in sorted(set(changed + untracked)):
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = root / name
        tracked = raw in changed
        diff = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-color', '--no-renames', '--unified=4', revision, '--', name) if tracked else b''
        old = b''
        if tracked:
            try:
                old = git(root, 'show', revision + ':' + name)
            except ValueError:
                pass
        new = b''
        omitted = None
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            omitted = 'Symlink content excluded'
        elif path.is_file():
            if path.stat().st_size > LIMIT:
                omitted = 'File exceeds 1 MB limit'
            else:
                new = path.read_bytes()
        if len(old) > LIMIT or len(diff) > LIMIT:
            omitted = 'File or diff exceeds 1 MB limit'
        if b'\0' in old or b'\0' in new:
            omitted = 'Binary content excluded'
        if omitted:
            old = new = diff = b''
        if not tracked and not omitted:
            lines = new.decode(errors='replace').splitlines()
            diff = ('--- /dev/null\n+++ b/' + name + '\n@@ -0,0 +1,' + str(len(lines)) + ' @@\n' + '\n'.join('+' + x for x in lines)).encode()
        files.append(dict(path=name, status='modified' if tracked and path.exists() else 'deleted' if tracked else 'untracked',
                          diff=diff.decode(errors='replace'), old=old.decode(errors='replace'), new=new.decode(errors='replace'), omitted=omitted))
    snapshot = dict(repository=root.name, base=revision, files=files)
    snapshot['id'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]
    return snapshot

def validate_report(report, snapshot):
    if not isinstance(report, dict) or report.get('snapshotId') != snapshot['id']:
        raise ValueError('snapshotId must match the current snapshot')
    for key in ('title', 'summary'):
        if not isinstance(report.get(key), str):
            raise ValueError(key + ' must be text')
    files = {f['path']: f for f in snapshot['files']}
    findings = report.get('findings', [])
    if not isinstance(findings, list) or len(findings) > 200:
        raise ValueError('findings must be an array of at most 200 items')
    for f in findings:
        if not isinstance(f, dict) or f.get('priority') not in ('P0', 'P1', 'P2', 'P3'):
            raise ValueError('Each finding needs priority P0–P3')
        if any(not isinstance(f.get(k), str) or not f[k] for k in ('title', 'detail')):
            raise ValueError('Each finding needs title and detail')
        evidence = f.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('Each finding needs code evidence')
        for e in evidence:
            if not isinstance(e, dict) or e.get('path') not in files or e.get('side') not in ('old', 'new'):
                raise ValueError('Evidence must reference a snapshot file and old/new side')
            lines = files[e['path']][e['side']].splitlines()
            line = e.get('line')
            if type(line) is not int or not 1 <= line <= len(lines) or e.get('quote') != lines[line - 1]:
                raise ValueError('Evidence line and exact quote do not match collected code')
    report['findings'] = findings
    diagram = report.get('diagram', {})
    if not isinstance(diagram, dict):
        raise ValueError('diagram must be an object')
    nodes, edges = diagram.get('nodes', []), diagram.get('edges', [])
    if not isinstance(nodes, list) or len(nodes) > 30 or any(not isinstance(n, str) for n in nodes) or len(set(nodes)) != len(nodes):
        raise ValueError('diagram.nodes must contain at most 30 unique text labels')
    if not isinstance(edges, list) or any(not isinstance(e, list) or len(e) != 2 or any(n not in nodes for n in e) for e in edges):
        raise ValueError('diagram.edges must reference node labels')
    return report

class Session:
    def __init__(self, repo, base):
        self.repo, self.base = repo, base
        self.snapshot = collect(repo, base)
        self.report = None
        self.token = secrets.token_urlsafe(32)
        self.last_seen = time.monotonic()
        self.lock = threading.RLock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, data, mime='application/json'):
        body = json.dumps(data).encode() if mime == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        s = self.server.session
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            return self.respond(403, {'error': 'Invalid host'})
        prefix = '/' + s.token + '/'
        if not self.path.startswith(prefix):
            return self.respond(403, {'error': 'Invalid session'})
        route = self.path[len(prefix):]
        if self.command == 'GET':
            if route == 'state':
                with s.lock:
                    return self.respond(200, {'snapshot': s.snapshot, 'report': s.report})
            if route in ('', 'app.js', 'style.css'):
                filename = route or 'index.html'
                mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript', 'style.css': 'text/css'}[filename]
                return self.respond(200, (Path(__file__).parent / 'web' / filename).read_bytes(), mime)
        if self.command == 'POST':
            origin = self.headers.get('Origin')
            if origin and origin != f'http://127.0.0.1:{self.server.server_port}':
                return self.respond(403, {'error': 'Invalid origin'})
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 2_000_000:
                return self.respond(413, {'error': 'Payload too large'})
            payload = self.rfile.read(size)
            with s.lock:
                if route == 'heartbeat':
                    s.last_seen = time.monotonic()
                    return self.respond(200, {'ok': True})
                if route == 'close':
                    self.respond(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if route == 'refresh':
                    s.snapshot = collect(s.repo, s.base)
                    s.report = None
                    return self.respond(200, {'ok': True})
                if route == 'report':
                    if collect(s.repo, s.base)['id'] != s.snapshot['id']:
                        raise ValueError('Repository changed; refresh and review again')
                    s.report = validate_report(json.loads(payload), s.snapshot)
                    return self.respond(200, {'ok': True})
        self.respond(404, {'error': 'Not found'})

    def do_GET(self):
        self.safe_dispatch()

    def do_POST(self):
        self.safe_dispatch()

    def safe_dispatch(self):
        try:
            self.dispatch()
        except (ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
            self.respond(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('repo', nargs='?', default='.')
    serve.add_argument('--base', default='HEAD')
    serve.add_argument('--no-open', action='store_true')
    serve.add_argument('--idle-timeout', type=int, default=120)
    submit = sub.add_parser('submit')
    submit.add_argument('url', help='Session URL printed by serve')
    args = parser.parse_args()
    if args.command == 'submit':
        import sys
        url = args.url
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
            parser.error('Use a loopback session URL')
        req = urllib.request.Request(url.rstrip('/') + '/report', data=sys.stdin.buffer.read(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            print(response.read().decode())
        return
    if args.idle_timeout < 30:
        parser.error('--idle-timeout must be at least 30 seconds')
    session = Session(args.repo, args.base)
    server = LocalServer(('127.0.0.1', 0), Handler)
    server.session = session
    url = f'http://127.0.0.1:{server.server_port}/{session.token}/'
    print(url, flush=True)
    def expire():
        while time.monotonic() - session.last_seen < args.idle_timeout:
            time.sleep(2)
        server.shutdown()
    threading.Thread(target=expire, daemon=True).start()
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        session.snapshot.clear()
        session.report = None

if __name__ == '__main__':
    main()
