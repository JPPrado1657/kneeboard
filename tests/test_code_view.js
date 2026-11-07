// Run with: node tests/test_code_view.js (no browser or dependencies required).
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('kneeboard/web/app.js', 'utf8');
class Node {
  constructor(tag, cls = '', text = '') { this.tag = tag; this.className = cls; this.textContent = text; this.children = []; this.dataset = {}; this.style = {}; this.scrollLeft = this.scrollTop = 0; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute() {}
  querySelector() { return null; }
  replaceChildren(fragment) { this.children = fragment.children; }
}
const context = vm.createContext({
  el:(...args) => new Node(...args),
  localStorage:{getItem:() => '{invalid'},
  document:{createDocumentFragment:() => new Node('fragment')},
});
vm.runInContext(source.slice(source.indexOf('const codePreferences ='), source.indexOf('const codeViews =')) + '\nthis.testAPI = {diffLines, codeLine, paintCode, codePreferences};', context);
const {diffLines, codeLine, paintCode, codePreferences} = context.testAPI;
const rows = diffLines({diff:'--- a/a.js\n+++ b/a.js\n@@ -9,3 +20,4 @@\n---old content\n+++new content\n+<script>alert(1)</script>\n same\n@@ -40 +60 @@\n-last\n+next'});
assert.equal(rows[3].old, 9);
assert.equal(rows[4].now, 20);
assert.equal(rows[5].now, 21);
assert.equal(rows[6].old, 10);
assert.equal(rows[6].now, 22);
assert.equal(rows[8].old, 40);
assert.equal(rows[9].now, 60);
assert.equal(rows[3].kind, 'del'); // --- inside a hunk is code, not a file header.
assert.equal(rows[4].kind, 'add');
const line = codeLine(rows[5]);
assert.equal(line.children.at(-1).textContent, '<script>alert(1)</script>');
assert.equal(line.dataset.newLine, 21);
const code = new Node('div');
paintCode(code, rows);
assert.equal(code.children.length, rows.length - 2); // Hide metadata, retain hunks.
codePreferences.view = 'split'; codePreferences.wrap = true;
paintCode(code, rows);
assert.equal(code.className, 'code split wrap');
const pairs = code.children.filter(node => node.className === 'split-row');
assert.equal(pairs.length, 4);
assert.equal(pairs[0].children[0].dataset.oldLine, 9);
assert.equal(pairs[0].children[1].dataset.newLine, 20);
assert.equal(pairs[1].children[0].className, 'line split-empty');
assert.equal(pairs[1].children[1].dataset.newLine, 21);
assert.equal(pairs[2].children[0].dataset.oldLine, 10);
assert.equal(pairs[2].children[1].dataset.newLine, 22);
const omitted = new Node('div');
paintCode(omitted, diffLines({omitted:'Binary content excluded'}));
assert.equal(omitted.children.at(-1).children.at(-1).textContent, 'Binary content excluded');
console.log('Code view checks passed: line mapping, split alignment, omitted files, safe text, metadata.');
