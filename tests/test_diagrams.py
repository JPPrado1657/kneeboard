from pathlib import Path
import hashlib
import unittest

from kneeboard.app import validate_diagram


class DiagramTests(unittest.TestCase):
    def test_accepts_supported_diagram_sources_and_legacy_graph(self):
        for diagram in [
            dict(nodes=['API', 'Web'], edges=[['Web', 'API']]),
            dict(format='mermaid', source='flowchart LR\n  Web --> API'),
            dict(format='drawio', source='<mxGraphModel><root><mxCell id="0"/></root></mxGraphModel>'),
            dict(format='drawio', source='<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>'),
            dict(format='svg', source='<svg xmlns="http://www.w3.org/2000/svg"><text>Diagram</text></svg>')
        ]:
            with self.subTest(format=diagram.get('format', 'graph')):
                validate_diagram(diagram)

    def test_rejects_unsupported_sources_and_xml_entities(self):
        invalid = [
            dict(format='unknown', source='diagram'),
            dict(format='mermaid', source=''),
            dict(format='mermaid', source='x' * 100001),
            dict(format='mermaid', source='%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA-->B'),
            dict(format='mermaid', source='---\nconfig: {}\n---\nflowchart LR\nA-->B'),
            dict(format='drawio', source='<mxfile><diagram>compressed-payload</diagram></mxfile>'),
            dict(format='drawio', source='<svg/>'),
            dict(format='drawio', source='<mxGraphModel>'),
            dict(format='svg', source='<html/>'),
            dict(format='svg', source='<!DOCTYPE svg [<!ENTITY x "data">]><svg>&x;</svg>'),
            dict(nodes=['API'], edges=[['API', 'Unknown']])
        ]
        for diagram in invalid:
            with self.subTest(diagram=str(diagram)[:100]), self.assertRaises(ValueError):
                validate_diagram(diagram)

    def test_bundled_viewers_match_pinned_upstream_bytes(self):
        root = Path(__file__).parents[1] / 'kneeboard/web/vendor'
        for name, digest in [
            ('mermaid.min.js', '336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3'),
            ('viewer-static.min.js', '35b8ef54369e938ad2399d3c8ce3945591d4b200a01f6942d0e39743bea1f1a1')
        ]:
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
