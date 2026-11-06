# Bundled diagram renderers

These are upstream browser bundles, served locally. They are installed with the Python package; users do not need Node.js or a CDN connection to render reports.

| Bundle | Version | SHA-256 | Upstream |
| --- | --- | --- | --- |
| `mermaid.min.js` | 12.3.0 | `336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3` | https://cdn.jsdelivr.net/npm/mermaid@12.3.0/dist/mermaid.min.js |
| `viewer-static.min.js` | draw.io v32.6.10 | `35b8ef54369e938ad2399d3c8ce3945591d4b200a01f6942d0e39743bea1f1a1` | https://raw.githubusercontent.com/jgraph/drawio/v32.6.10/src/main/webapp/js/viewer-static.min.js |

Mermaid is MIT licensed; draw.io's source code is Apache-2.0 licensed. License files are included alongside the bundles. Upstream bundle comments and embedded dependency notices are preserved. draw.io's icon/stencil/template assets have separate upstream terms; no external asset packs or stencil XML are distributed here. The read-only viewer supports built-in shapes; external images, custom stencils, MathJax, hosted services, and the editor are not loaded.

Maintainers can reproduce the downloads and verify their hashes with `python3 scripts/vendor_diagrams.py`. No script patches are applied to the upstream bundles. The application adapter configures offline behavior and confines rendering to an opaque-origin sandbox with network connections disabled.
