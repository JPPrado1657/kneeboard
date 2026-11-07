import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

class LocalServer(ThreadingHTTPServer):
    def server_bind(self):
        # A loopback-only server does not need reverse DNS (which can block).
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


LIMIT = 1_000_000
DISCONNECT_GRACE = 30
REVIEW_LEASE_SECONDS = 1800

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
    repository_id = hashlib.sha256(os.fsencode(str(root))).hexdigest()[:16]
    snapshot = dict(repository=root.name, repositoryId=repository_id, base=revision, files=files)
    snapshot['id'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]
    return snapshot

def validate_text(item, keys):
    if not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in keys):
        raise ValueError('Expected text fields: ' + ', '.join(keys))


def validate_evidence(evidence, snapshots, repository_id=None):
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('Each finding or behavior needs code evidence')
    for entry in evidence:
        if not isinstance(entry, dict):
            raise ValueError('Evidence must be a JSON object')
        target = entry.get('repositoryId', repository_id)
        if not isinstance(target, str) or target not in snapshots or (repository_id and target != repository_id):
            raise ValueError('Evidence must reference the correct repositoryId')
        files = {file['path']: file for file in snapshots[target]['files']}
        if not isinstance(entry.get('path'), str) or entry['path'] not in files or entry.get('side') not in ('old', 'new'):
            raise ValueError('Evidence must reference a snapshot file and old/new side')
        lines = files[entry['path']][entry['side']].splitlines()
        line = entry.get('line')
        if type(line) is not int or not 1 <= line <= len(lines) or entry.get('quote') != lines[line - 1]:
            raise ValueError('Evidence line and exact quote do not match collected code')


def validate_findings(findings, snapshots, repository_id=None):
    if not isinstance(findings, list) or len(findings) > 200:
        raise ValueError('findings must be an array of at most 200 items')
    for finding in findings:
        validate_text(finding, ('title', 'detail'))
        if finding.get('priority') not in ('P0', 'P1', 'P2', 'P3'):
            raise ValueError('Each finding needs priority P0–P3')
        validate_evidence(finding.get('evidence'), snapshots, repository_id)


def validate_diagram(diagram):
    if not isinstance(diagram, dict):
        raise ValueError('diagram must be an object')
    format = diagram.get('format', 'graph')
    if format not in ('graph', 'mermaid', 'drawio', 'svg'):
        raise ValueError('Diagram format must be graph, mermaid, drawio, or svg')
    if format != 'graph':
        source = diagram.get('source')
        if not isinstance(source, str) or not source.strip() or len(source) > 100_000:
            raise ValueError('Diagram source must contain 1–100000 characters')
        if format == 'mermaid':
            if '%%{' in source or source.lstrip().startswith('---'):
                raise ValueError('Mermaid configuration directives and frontmatter are disabled; submit diagram syntax only')
        else:
            if '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper():
                raise ValueError('Diagram XML must not contain DTDs or entities')
            try:
                root = ET.fromstring(source)
            except ET.ParseError as exc:
                raise ValueError('Invalid diagram XML: ' + str(exc)) from exc
            if format == 'svg':
                if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg'):
                    raise ValueError('SVG source must have an svg root')
            elif root.tag == 'mxfile':
                pages = root.findall('diagram')
                if not pages or len(pages) > 10 or any(page.find('mxGraphModel') is None for page in pages):
                    raise ValueError('draw.io requires uncompressed mxfile pages containing mxGraphModel')
            elif root.tag != 'mxGraphModel':
                raise ValueError('draw.io source must be an uncompressed mxGraphModel or mxfile')
        return
    nodes, edges = diagram.get('nodes', []), diagram.get('edges', [])
    if not isinstance(nodes, list) or len(nodes) > 30 or any(not isinstance(node, str) for node in nodes) or len(set(nodes)) != len(nodes):
        raise ValueError('diagram.nodes must contain at most 30 unique text labels')
    if not isinstance(edges, list) or any(not isinstance(edge, list) or len(edge) != 2 or any(node not in nodes for node in edge) for edge in edges):
        raise ValueError('diagram.edges must reference node labels')


def validate_report(report, snapshot):
    if not isinstance(report, dict) or report.get('snapshotId') != snapshot['id']:
        raise ValueError('snapshotId must match the current snapshot')
    validate_text(report, ('title', 'summary'))
    report['findings'] = report.get('findings', [])
    validate_findings(report['findings'], {snapshot['repositoryId']: snapshot}, snapshot['repositoryId'])
    validate_diagram(report.get('diagram', {}))
    return report


def validate_overview(report, repositories):
    expected = {repo['id']: repo['snapshot']['id'] for repo in repositories}
    if not isinstance(report, dict) or report.get('snapshotIds') != expected:
        raise ValueError('snapshotIds must match every repository snapshot in the session')
    validate_text(report, ('title', 'summary'))
    snapshots = {repo['id']: repo['snapshot'] for repo in repositories}
    report['findings'] = report.get('findings', [])
    validate_findings(report['findings'], snapshots)
    behaviors = report.get('behaviors', [])
    if not isinstance(behaviors, list) or len(behaviors) > 100:
        raise ValueError('behaviors must be an array of at most 100 items')
    for behavior in behaviors:
        validate_text(behavior, ('title', 'detail'))
        validate_evidence(behavior.get('evidence'), snapshots)
    report['behaviors'] = behaviors
    diagrams = report.get('diagrams', [])
    if not isinstance(diagrams, list) or len(diagrams) > 10:
        raise ValueError('diagrams must be an array of at most 10 items')
    for diagram in diagrams:
        validate_text(diagram, ('title',))
        validate_diagram(diagram)
    report['diagrams'] = diagrams
    return report

class Session:
    def __init__(self, repos, base, state_dir=None):
        if isinstance(repos, (str, os.PathLike)):
            repos = [repos]
        self.base = base
        self.repositories = []
        roots = set()
        for repo in repos:
            root = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
            if root in roots:
                raise ValueError('Repository specified more than once: ' + str(root))
            roots.add(root)
            self.repositories.append(dict(path=str(root)))
        if not self.repositories:
            raise ValueError('Specify at least one repository')
        identity = {'paths': sorted(str(root) for root in roots), 'base': base}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        directory = Path(state_dir or os.environ.get('KNEEBOARD_STATE_DIR') or
                         Path.home() / '.local' / 'share' / 'kneeboard')
        self.state_path = directory / (key + '.json')
        self.overview_report = None
        if self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text())
                if saved['version'] != 1 or saved['identity'] != identity:
                    raise ValueError('Incompatible saved session')
                by_path = {repo['path']: repo for repo in saved['repositories']}
                restored = [by_path[repo['path']] for repo in self.repositories]
                for repo in restored:
                    if not isinstance(repo['snapshot']['files'], list):
                        raise ValueError('Invalid saved snapshot')
                    if repo['report'] is not None:
                        validate_report(repo['report'], repo['snapshot'])
                overview = saved['overview_report']
                if overview is not None:
                    validate_overview(overview, restored)
                self.repositories = restored
                self.overview_report = overview
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f'Could not restore saved session at {self.state_path}: {exc}') from exc
        else:
            for repo in self.repositories:
                root = Path(repo['path'])
                snapshot = collect(root, base)
                repo.update(id=snapshot['repositoryId'], name=root.name, snapshot=snapshot, report=None)
        self.identity = identity
        self.save()
        self.token = secrets.token_urlsafe(32)
        self.last_seen = time.monotonic()
        self.clients = {}
        self.review_leases = {}
        self.disconnect_deadline = None
        self.lock = threading.RLock()

    def save(self):
        """Replace the saved review atomically; runtime credentials stay in memory."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {'version': 1, 'identity': self.identity, 'repositories': self.repositories,
                'overview_report': self.overview_report}
        descriptor, temporary = tempfile.mkstemp(prefix='.session-', dir=self.state_path.parent)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def heartbeat(self, client_id=None):
        now = time.monotonic()
        self.last_seen = now
        self.disconnect_deadline = None
        if client_id is not None:
            if not isinstance(client_id, str) or not 1 <= len(client_id) <= 100:
                raise ValueError('clientId must contain 1–100 characters')
            self.clients[client_id] = now

    def disconnect(self, client_id):
        if not isinstance(client_id, str) or not client_id:
            raise ValueError('clientId is required')
        if client_id in self.clients:
            del self.clients[client_id]
            if not self.clients:
                self.disconnect_deadline = time.monotonic() + DISCONNECT_GRACE

    def start_review(self, lease_id=None):
        if lease_id is None:
            lease_id = secrets.token_urlsafe(24)
        elif not isinstance(lease_id, str) or lease_id not in self.review_leases:
            raise ValueError('Unknown review lease; connect without --lease to start a new review')
        self.review_leases[lease_id] = time.monotonic() + REVIEW_LEASE_SECONDS
        return lease_id

    def finish_review(self, lease_id):
        if not isinstance(lease_id, str) or not lease_id:
            raise ValueError('leaseId is required')
        if self.review_leases.pop(lease_id, None) is not None:
            # Let a backgrounded report tab resume after the agent releases its lease.
            # An explicitly disconnected last tab still follows its close deadline.
            self.last_seen = time.monotonic()

    def expired(self, idle_timeout, now=None):
        now = time.monotonic() if now is None else now
        self.clients = {key:seen for key, seen in self.clients.items() if now - seen < idle_timeout}
        self.review_leases = {key:until for key, until in self.review_leases.items() if now < until}
        if self.clients or self.review_leases:
            return False
        if self.disconnect_deadline is not None:
            return now >= self.disconnect_deadline
        return now - self.last_seen >= idle_timeout

    @property
    def snapshot(self):
        return self.repositories[0]['snapshot']

    @property
    def report(self):
        return self.repositories[0]['report']

    def repository(self, repository_id=None):
        if repository_id is None and len(self.repositories) == 1:
            return self.repositories[0]
        for repo in self.repositories:
            if repo['id'] == repository_id:
                return repo
        raise ValueError('Specify a valid repositoryId from state.repositories')

    def state(self):
        state = {'repositories': self.repositories,
                 'overview': {'snapshotIds': self.snapshot_ids(), 'report': self.overview_report},
                 'connection': {'command': [sys.executable, '-m', 'kneeboard.app'],
                                'activeReviews': len(self.review_leases)}}
        # Preserve the original API for single-repository clients.
        if len(self.repositories) == 1:
            state.update(snapshot=self.snapshot, report=self.report)
        return state

    def refresh(self, repository_id=None):
        repo = self.repository(repository_id)
        repo['snapshot'] = collect(repo['path'], self.base)
        repo['report'] = None
        self.overview_report = None
        self.save()

    def snapshot_ids(self):
        return {repo['id']: repo['snapshot']['id'] for repo in self.repositories}

    def refresh_all(self):
        snapshots = [collect(repo['path'], self.base) for repo in self.repositories]
        for repo, snapshot in zip(self.repositories, snapshots):
            repo.update(snapshot=snapshot, report=None)
        self.overview_report = None
        self.save()

    def submit_overview(self, report):
        for repo in self.repositories:
            if collect(repo['path'], self.base)['id'] != repo['snapshot']['id']:
                raise ValueError('Repository changed; refresh and review again: ' + repo['name'])
        self.overview_report = validate_overview(report, self.repositories)
        self.save()

    def submit(self, report):
        if not isinstance(report, dict):
            raise ValueError('Report must be a JSON object')
        repo = self.repository(report.get('repositoryId'))
        if collect(repo['path'], self.base)['id'] != repo['snapshot']['id']:
            raise ValueError('Repository changed; refresh and review again')
        repo['report'] = validate_report(report, repo['snapshot'])
        self.save()

    def clear(self):
        for repo in self.repositories:
            repo['snapshot'].clear()
            repo['report'] = None
        self.repositories.clear()
        self.overview_report = None
        self.clients.clear()
        self.review_leases.clear()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, data, mime='application/json', diagram=False):
        body = json.dumps(data).encode() if mime == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        policy = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                  "img-src data: blob:; font-src 'self' data:; connect-src 'none'; "
                  "frame-ancestors 'self'; base-uri 'none'; form-action 'none'" if diagram else
                  "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                  "frame-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Content-Security-Policy', policy)
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
                    return self.respond(200, s.state())
            assets = {'': ('index.html', 'text/html; charset=utf-8'),
                      'app.js': ('app.js', 'text/javascript'), 'style.css': ('style.css', 'text/css'),
                      'diagram.html': ('diagram.html', 'text/html; charset=utf-8'),
                      'diagram.js': ('diagram.js', 'text/javascript'),
                      'diagram.css': ('diagram.css', 'text/css'),
                      'vendor/mermaid.min.js': ('vendor/mermaid.min.js', 'text/javascript'),
                      'vendor/viewer-static.min.js': ('vendor/viewer-static.min.js', 'text/javascript')}
            if route in assets:
                filename, mime = assets[route]
                return self.respond(200, (Path(__file__).parent / 'web' / filename).read_bytes(), mime,
                                    diagram=route == 'diagram.html')
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
                    data = json.loads(payload or b'{}')
                    if not isinstance(data, dict):
                        raise ValueError('Heartbeat body must be a JSON object')
                    s.heartbeat(data.get('clientId'))
                    return self.respond(200, {'ok': True})
                if route in ('disconnect', 'review/start', 'review/finish'):
                    data = json.loads(payload or b'{}')
                    if not isinstance(data, dict):
                        raise ValueError('Body must be a JSON object')
                    if route == 'disconnect':
                        s.disconnect(data.get('clientId'))
                    elif route == 'review/start':
                        lease = s.start_review(data.get('leaseId'))
                        return self.respond(200, {'leaseId': lease, 'expiresInSeconds': REVIEW_LEASE_SECONDS,
                                                  'state': s.state()})
                    else:
                        s.finish_review(data.get('leaseId'))
                    return self.respond(200, {'ok': True})
                if route == 'close':
                    self.respond(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if route == 'refresh-all':
                    s.refresh_all()
                    return self.respond(200, {'ok': True})
                if route == 'overview':
                    s.submit_overview(json.loads(payload))
                    return self.respond(200, {'ok': True})
                if route == 'refresh':
                    data = json.loads(payload or b'{}')
                    if not isinstance(data, dict):
                        raise ValueError('Refresh body must be a JSON object')
                    s.refresh(data.get('repositoryId'))
                    return self.respond(200, {'ok': True})
                if route == 'report':
                    s.submit(json.loads(payload))
                    return self.respond(200, {'ok': True})
        self.respond(404, {'error': 'Not found'})

    def do_GET(self):
        self.safe_dispatch()

    def do_POST(self):
        self.safe_dispatch()

    def safe_dispatch(self):
        try:
            self.dispatch()
        except OSError:
            self.respond(500, {'error': 'Could not save review data; check local storage permissions and space'})
        except (ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
            self.respond(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('repos', nargs='*', default=['.'], help='One or more repository paths (default: current directory)')
    serve.add_argument('--base', default='HEAD')
    serve.add_argument('--no-open', action='store_true')
    serve.add_argument('--idle-timeout', type=int, default=120)
    submit = sub.add_parser('submit')
    submit.add_argument('url', help='Session URL printed by serve')
    target = submit.add_mutually_exclusive_group()
    target.add_argument('--overview', action='store_true', help='Submit a system overview covering every repository')
    target.add_argument('--repo', help='Repository ID from state.repositories; required for multi-repository sessions unless supplied in JSON')
    connect = sub.add_parser('connect', help='Attach an AI review to an existing session without opening or closing tabs')
    connect.add_argument('url')
    connect.add_argument('--lease', help='Renew an existing review lease')
    finish = sub.add_parser('finish', help='Release an AI review lease; leave the browser session running')
    finish.add_argument('url')
    finish.add_argument('--lease', required=True)
    args = parser.parse_args()
    if args.command in ('submit', 'connect', 'finish'):
        url = args.url
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.query or parsed.fragment or parsed.username or parsed.password:
            parser.error('Use a loopback session URL')
        if args.command == 'submit':
            payload = sys.stdin.buffer.read()
            if args.repo:
                try:
                    report = json.loads(payload)
                except ValueError as exc:
                    parser.error(str(exc))
                if not isinstance(report, dict):
                    parser.error('Report must be a JSON object')
                if report.get('repositoryId', args.repo) != args.repo:
                    parser.error('--repo conflicts with repositoryId in report')
                report['repositoryId'] = args.repo
                payload = json.dumps(report).encode()
            route = 'overview' if args.overview else 'report'
        else:
            payload = json.dumps({'leaseId': args.lease}).encode()
            route = 'review/start' if args.command == 'connect' else 'review/finish'
        req = urllib.request.Request(url.rstrip('/') + '/' + route, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                print(response.read().decode())
        except urllib.error.HTTPError as exc:
            parser.exit(1, 'Session rejected the request: ' + exc.read().decode(errors='replace') + '\n')
        except (urllib.error.URLError, TimeoutError) as exc:
            parser.exit(1, 'Could not reach the running session: ' + str(exc) + '\n')
        return
    if args.idle_timeout < 30:
        parser.error('--idle-timeout must be at least 30 seconds')
    try:
        session = Session(args.repos, args.base)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    server = LocalServer(('127.0.0.1', 0), Handler)
    server.session = session
    url = f'http://127.0.0.1:{server.server_port}/{session.token}/'
    print(url, flush=True)
    print("Click Connect AI in the report to attach your assistant to this session.", flush=True)
    def expire():
        while True:
            with session.lock:
                expired = session.expired(args.idle_timeout)
            if expired:
                server.shutdown()
                return
            time.sleep(2)
    threading.Thread(target=expire, daemon=True).start()
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        session.clear()

if __name__ == '__main__':
    main()
