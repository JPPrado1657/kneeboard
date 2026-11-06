import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
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
    def __init__(self, repos, base):
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
            snapshot = collect(root, base)
            self.repositories.append(dict(id=snapshot['repositoryId'], name=root.name,
                                          path=str(root), snapshot=snapshot, report=None))
        if not self.repositories:
            raise ValueError('Specify at least one repository')
        self.overview_report = None
        self.token = secrets.token_urlsafe(32)
        self.last_seen = time.monotonic()
        self.lock = threading.RLock()

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
                 'overview': {'snapshotIds': self.snapshot_ids(), 'report': self.overview_report}}
        # Preserve the original API for single-repository clients.
        if len(self.repositories) == 1:
            state.update(snapshot=self.snapshot, report=self.report)
        return state

    def refresh(self, repository_id=None):
        repo = self.repository(repository_id)
        repo['snapshot'] = collect(repo['path'], self.base)
        repo['report'] = None
        self.overview_report = None

    def snapshot_ids(self):
        return {repo['id']: repo['snapshot']['id'] for repo in self.repositories}

    def refresh_all(self):
        snapshots = [collect(repo['path'], self.base) for repo in self.repositories]
        for repo, snapshot in zip(self.repositories, snapshots):
            repo.update(snapshot=snapshot, report=None)
        self.overview_report = None

    def submit_overview(self, report):
        for repo in self.repositories:
            if collect(repo['path'], self.base)['id'] != repo['snapshot']['id']:
                raise ValueError('Repository changed; refresh and review again: ' + repo['name'])
        self.overview_report = validate_overview(report, self.repositories)

    def submit(self, report):
        if not isinstance(report, dict):
            raise ValueError('Report must be a JSON object')
        repo = self.repository(report.get('repositoryId'))
        if collect(repo['path'], self.base)['id'] != repo['snapshot']['id']:
            raise ValueError('Repository changed; refresh and review again')
        repo['report'] = validate_report(report, repo['snapshot'])

    def clear(self):
        for repo in self.repositories:
            repo['snapshot'].clear()
            repo['report'] = None
        self.repositories.clear()
        self.overview_report = None

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
                    s.last_seen = time.monotonic()
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
    args = parser.parse_args()
    if args.command == 'submit':
        import sys
        url = args.url
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
            parser.error('Use a loopback session URL')
        payload = sys.stdin.buffer.read()
        if args.repo:
            report = json.loads(payload)
            if not isinstance(report, dict):
                parser.error('Report must be a JSON object')
            if report.get('repositoryId', args.repo) != args.repo:
                parser.error('--repo conflicts with repositoryId in report')
            report['repositoryId'] = args.repo
            payload = json.dumps(report).encode()
        req = urllib.request.Request(url.rstrip('/') + ('/overview' if args.overview else '/report'), data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            print(response.read().decode())
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
        session.clear()

if __name__ == '__main__':
    main()
