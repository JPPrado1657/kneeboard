"""Reproduce the pinned diagram bundles. Development only; not part of setup."""
import hashlib
from pathlib import Path
import urllib.request

ASSETS = {
    'mermaid.min.js': ('https://cdn.jsdelivr.net/npm/mermaid@12.3.0/dist/mermaid.min.js',
                       '336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3'),
    'viewer-static.min.js': ('https://raw.githubusercontent.com/jgraph/drawio/v32.6.10/src/main/webapp/js/viewer-static.min.js',
                            '35b8ef54369e938ad2399d3c8ce3945591d4b200a01f6942d0e39743bea1f1a1'),
}


def main():
    destination = Path(__file__).resolve().parents[1] / 'kneeboard/web/vendor'
    downloads = {}
    for name, (url, expected) in ASSETS.items():
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Upstream checksum mismatch: ' + name)
        downloads[name] = data
    for name, data in downloads.items():
        (destination / name).write_bytes(data)
        print('Verified ' + name)


if __name__ == '__main__':
    main()
