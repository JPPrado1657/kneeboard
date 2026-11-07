let state, selected = '', selectedRepository = '', signature = '', ended = false;
const SYSTEM = 'system';
const clientId = crypto.randomUUID();
let diagramSignature = '', diagramFrames = [], previewEvidence = null, previewOpener = null, previewScroll = null;
window.addEventListener('message', event => {
  const entry = diagramFrames.find(item => item.frame.contentWindow === event.source);
  if (!entry || !event.data || typeof event.data !== 'object') return;
  if (event.data.type === 'kneeboard-diagram-ready') {
    entry.frame.contentWindow.postMessage({type:'kneeboard-diagram-render', format:entry.diagram.format, source:entry.diagram.source, expanded:entry.expanded}, '*');
  } else if (event.data.type === 'kneeboard-diagram-size') {
    const height = Number(event.data.height);
    if (entry.expanded) return;
    if (Number.isFinite(height)) entry.frame.height = Math.max(160, Math.min(660, height));
  } else if (event.data.type === 'kneeboard-diagram-expand' && !entry.expanded) {
    openDiagram(entry.diagram);
  } else if (event.data.type === 'kneeboard-diagram-rendered') {
    entry.status.textContent = 'Rendered locally';
  } else if (event.data.type === 'kneeboard-diagram-error') {
    entry.status.textContent = 'Diagram could not render: ' + String(event.data.message).slice(0,300);
  }
});
const $ = id => document.getElementById(id);
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache:'no-store'} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error);
  return data;
}
function repositoryLabel(repo) {
  return state.repositories.filter(item => item.name === repo.name).length > 1 ? repo.path : repo.name;
}
function switchView(id) {
  selectedRepository = id;
  selected = '';
  $('search').value = '';
  $('priority').value = '';
  $('error').textContent = '';
  render();
}
function focusEvidence(container, evidence) {
  const attribute = evidence.side === 'old' ? 'old-line' : 'new-line';
  const line = Array.from(container.querySelectorAll(`[data-${attribute}]`))
    .find(node => Number(node.dataset[evidence.side === 'old' ? 'oldLine' : 'newLine']) === evidence.line);
  if (line) {
    line.classList.add('focus');
    line.scrollIntoView({block:'center', inline:'nearest'});
  }
}
function jump(evidence) {
  if (evidence.repositoryId) selectedRepository = evidence.repositoryId;
  selected = evidence.path;
  $('search').value = '';
  $('priority').value = '';
  render();
  focusEvidence($('diffs'), evidence);
}
function cleanupPreview() {
  previewEvidence = null;
  diagramFrames = diagramFrames.filter(entry => !entry.expanded);
  $('previewContent').replaceChildren();
  $('previewActions').replaceChildren();
  document.body.classList.remove('preview-open');
  if (previewOpener) {
    (previewOpener.isConnected ? previewOpener : $('search')).focus({preventScroll:true});
    previewOpener = null;
  }
  if (previewScroll) { window.scrollTo(...previewScroll); previewScroll = null; }
}
function closePreview() {
  if ($('preview').open) { $('preview').close(); cleanupPreview(); }
}
function preparePreview(title) {
  closePreview();
  previewEvidence = null;
  diagramFrames = diagramFrames.filter(entry => !entry.expanded);
  previewOpener = document.activeElement;
  previewScroll = [window.scrollX, window.scrollY];
  $('previewTitle').textContent = title;
  $('previewContent').replaceChildren();
  $('previewActions').replaceChildren();
}
function showPreview() {
  document.body.classList.add('preview-open');
  $('preview').showModal();
}
function updateCodePreview() {
  const evidence = previewEvidence;
  const repo = state?.repositories.find(item => item.id === evidence.repositoryId);
  const file = repo?.snapshot.files.find(item => item.path === evidence.path);
  $('previewTitle').textContent = (repo ? repositoryLabel(repo) : 'Repository unavailable') + ' / ' + evidence.path;
  $('previewContent').replaceChildren(file ? renderDiff(file, true) : el('p', 'empty', 'This file is no longer available in the current snapshot.'));
  $('previewActions').replaceChildren();
  if (file) {
    const button = el('button', '', 'Open repository changes');
    button.onclick = () => { closePreview(); jump(evidence); };
    $('previewActions').append(button);
    focusEvidence($('previewContent'), evidence);
  }
}
function openCodePreview(evidence) {
  preparePreview('Code preview');
  previewEvidence = evidence;
  showPreview();
  updateCodePreview();
}
function openDiagram(diagram) {
  preparePreview(diagram.title || 'Change map');
  $('previewContent').append(renderDiagram(diagram, false, true));
  showPreview();
}
$('closePreview').onclick = closePreview;
$('preview').addEventListener('click', event => {
  const rect = $('preview').getBoundingClientRect();
  if (event.target === $('preview') && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) closePreview();
});
$('preview').addEventListener('close', () => { if (!$('preview').open) cleanupPreview(); });
function evidenceButtons(container, evidence, system) {
  const group = el('div', 'evidence-group');
  container.append(group);
  for (const entry of evidence) {
    const repo = state.repositories.find(item => item.id === entry.repositoryId);
    const prefix = system && repo ? repositoryLabel(repo) + ' / ' : '';
    const button = el('button', 'evidence', `${prefix}${entry.path}:${entry.line} (${entry.side}) ↗\n${entry.quote}`);
    button.onclick = () => system ? openCodePreview(entry) : jump(entry);
    group.append(button);
  }
}
function renderDiagram(diagram, system, expanded = false) {
  const panel = el('div', 'panel diagram');
  const format = diagram.format || 'graph';
  if (system) panel.append(el('h3', '', diagram.title));
  const toolbar = el('div', 'diagram-toolbar');
  toolbar.append(el('span', 'badge', format === 'drawio' ? 'DRAW.IO' : format.toUpperCase()));
  if (!expanded) {
    const expand = el('button', '', 'Expand diagram');
    expand.onclick = () => openDiagram(diagram);
    toolbar.append(expand);
  }
  panel.append(toolbar);
  if (format === 'graph') {
    const graph = el('div', 'graph-preview');
    if (!expanded) graph.onclick = () => openDiagram(diagram);
    panel.append(graph);
    let scale = 1;
    if (expanded) for (const [label, delta] of [['−', -.25], ['+', .25], ['Fit', 0]]) {
      const button = el('button', '', label);
      button.setAttribute('aria-label', label === 'Fit' ? 'Fit diagram' : 'Zoom ' + (delta > 0 ? 'in' : 'out') + ' diagram');
      button.onclick = () => { scale = delta ? Math.max(.5, Math.min(4, scale + delta)) : 1; graph.style.zoom = scale; };
      toolbar.append(button);
    }
    for (const node of diagram.nodes || []) graph.append(el('span', 'node', node));
    for (const [from, to] of diagram.edges || []) graph.append(el('div', 'edge', from + ' → ' + to));
    return panel;
  }
  const frame = el('iframe', 'diagram-frame');
  frame.title = (diagram.title || 'Change map') + ' (' + format + ')';
  frame.setAttribute('sandbox', 'allow-scripts');
  frame.setAttribute('referrerpolicy', 'no-referrer');
  frame.src = 'diagram.html'; frame.height = 240; frame.loading = 'lazy';
  for (const [label, direction] of [['−','out'],['+','in'],['Fit','reset']]) {
    const button = el('button', '', label);
    button.setAttribute('aria-label', (direction === 'reset' ? 'Fit' : 'Zoom ' + direction) + ' ' + (diagram.title || 'diagram'));
    button.onclick = () => frame.contentWindow?.postMessage({type:'kneeboard-diagram-zoom', direction}, '*');
    toolbar.append(button);
  }
  const status = el('small', 'diagram-status', 'Loading local renderer…');
  diagramFrames.push({frame, diagram, status, expanded});
  const details = el('details', 'diagram-source');
  details.append(el('summary', '', 'View ' + (format === 'drawio' ? 'draw.io XML' : format) + ' source'), el('pre', '', diagram.source));
  panel.append(toolbar, frame, status, details);
  return panel;
}
function renderDiagrams(diagrams, system) {
  const nextSignature = JSON.stringify([selectedRepository, diagrams]);
  if (nextSignature === diagramSignature) return;
  diagramSignature = nextSignature; diagramFrames = diagramFrames.filter(entry => entry.expanded);
  $('diagram').replaceChildren();
  for (const diagram of diagrams) $('diagram').append(renderDiagram(diagram, system));
  if (!diagrams.length) $('diagram').append(el('div', 'empty', system
    ? 'Your AI can supply architecture, request-flow, and behavior diagrams spanning the project.'
    : 'Your AI can supply a change map with labeled components and connections.'));
}
// Only display preferences go into browser storage; captured code stays server-side.
const codePreferences = {view:'unified', wrap:false, size:13};
try {
  const saved = JSON.parse(localStorage.getItem('kneeboard.codeView') || 'null');
  if (saved && ['unified','split'].includes(saved.view)) codePreferences.view = saved.view;
  if (typeof saved?.wrap === 'boolean') codePreferences.wrap = saved.wrap;
  if (Number.isInteger(saved?.size)) codePreferences.size = Math.max(11, Math.min(18, saved.size));
} catch (_) { /* Storage may be unavailable in private browser sessions. */ }
function diffLines(file) {
  let old = 0, now = 0, inHunk = false;
  return (file.omitted || file.diff || 'No textual diff available').split('\n').map(text => {
    const hunk = text.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
    if (hunk) { old = +hunk[1]; now = +hunk[2]; inHunk = true; }
    const row = {text, kind:hunk ? 'hunk' : 'meta', old:null, now:null};
    if (!hunk && inHunk) {
      if (text.startsWith('+')) { row.kind = 'add'; row.now = now++; }
      else if (text.startsWith('-')) { row.kind = 'del'; row.old = old++; }
      else if (text.startsWith(' ')) { row.kind = 'context'; row.old = old++; row.now = now++; }
    }
    return row;
  });
}
function codeLine(row, side) {
  const node = el('div', 'line ' + row.kind);
  if (row.old !== null && side !== 'new') node.dataset.oldLine = row.old;
  if (row.now !== null && side !== 'old') node.dataset.newLine = row.now;
  for (const number of side ? [side === 'old' ? row.old : row.now] : [row.old, row.now]) {
    const gutter = el('span', 'line-number', number === null ? '' : String(number));
    gutter.setAttribute('aria-hidden', 'true'); node.append(gutter);
  }
  const isCode = ['add','del','context'].includes(row.kind);
  const marker = el('span', 'line-marker', row.kind === 'add' ? '+' : row.kind === 'del' ? '−' : '');
  marker.setAttribute('aria-hidden', 'true');
  node.append(marker, el('span', 'line-text', isCode ? row.text.slice(1) : row.text));
  return node;
}
function paintCode(code, rows) {
  const scroll = [code.scrollLeft, code.scrollTop];
  const focused = code.querySelector('.focus');
  const evidence = focused ? {side:focused.dataset.newLine ? 'new' : 'old', line:Number(focused.dataset.newLine || focused.dataset.oldLine)} : null;
  code.className = 'code ' + codePreferences.view + (codePreferences.wrap ? ' wrap' : '');
  code.style.fontSize = codePreferences.size + 'px';
  const content = document.createDocumentFragment();
  const hasHunks = rows.some(row => row.kind === 'hunk');
  if (codePreferences.view === 'unified') {
    for (const row of rows) if (row.kind !== 'meta' || !hasHunks) content.append(codeLine(row));
  } else {
    const heading = el('div', 'split-head');
    heading.append(el('span', '', 'BEFORE'), el('span', '', 'AFTER')); content.append(heading);
    const pair = (left, right) => {
      const node = el('div', 'split-row');
      node.append(left ? codeLine(left, 'old') : el('div', 'line split-empty'), right ? codeLine(right, 'new') : el('div', 'line split-empty'));
      content.append(node);
    };
    for (let index = 0; index < rows.length;) {
      const row = rows[index];
      if (row.kind === 'del' || row.kind === 'add') {
        const removed = [], added = [];
        while (index < rows.length && rows[index].kind === 'del') removed.push(rows[index++]);
        while (index < rows.length && rows[index].kind === 'add') added.push(rows[index++]);
        for (let i = 0; i < Math.max(removed.length, added.length); i++) pair(removed[i], added[i]);
      } else {
        if (row.kind === 'context') pair(row, row);
        else if (row.kind !== 'meta' || !hasHunks) content.append(codeLine(row));
        index++;
      }
    }
  }
  code.replaceChildren(content);
  if (evidence) {
    const attribute = evidence.side === 'new' ? 'newLine' : 'oldLine';
    Array.from(code.querySelectorAll('.line')).find(node => Number(node.dataset[attribute]) === evidence.line)?.classList.add('focus');
  }
  code.scrollLeft = scroll[0]; code.scrollTop = scroll[1];
}
const codeViews = new WeakMap();
function changeCodePreferences(change) {
  Object.assign(codePreferences, change);
  try { localStorage.setItem('kneeboard.codeView', JSON.stringify(codePreferences)); } catch (_) {}
  for (const box of document.querySelectorAll('.diff')) codeViews.get(box)?.();
}
function renderDiff(file, expanded = false) {
  const box = el('article', 'diff');
  const rows = diffLines(file);
  const header = el('div', 'diff-header');
  header.append(el('h3', '', file.path), el('span', 'file-status', file.status));
  const toolbar = el('div', 'code-toolbar');
  toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', 'Code view for ' + file.path);
  const options = el('div', 'code-options'), toggle = el('div', 'view-toggle');
  const buttons = [];
  for (const [view, label] of [['unified','Unified'], ['split','Split']]) {
    const button = el('button', '', label);
    button.onclick = () => changeCodePreferences({view});
    buttons.push([button, view]); toggle.append(button);
  }
  const wrap = el('button', '', 'Wrap lines');
  wrap.onclick = () => changeCodePreferences({wrap:!codePreferences.wrap});
  const smaller = el('button', 'code-size', 'A−'), larger = el('button', 'code-size', 'A+');
  smaller.setAttribute('aria-label', 'Decrease code font size'); larger.setAttribute('aria-label', 'Increase code font size');
  smaller.onclick = () => changeCodePreferences({size:Math.max(11, codePreferences.size - 1)});
  larger.onclick = () => changeCodePreferences({size:Math.min(18, codePreferences.size + 1)});
  const size = el('span', 'code-size-label');
  options.append(toggle, wrap, smaller, size, larger);
  const summary = el('span', 'diff-summary');
  summary.append(el('span', 'added', '+' + rows.filter(row => row.kind === 'add').length), el('span', 'removed', '−' + rows.filter(row => row.kind === 'del').length));
  summary.setAttribute('aria-label', rows.filter(row => row.kind === 'add').length + ' added lines, ' + rows.filter(row => row.kind === 'del').length + ' removed lines');
  const actions = el('div', 'code-options'); actions.append(summary);
  if (!expanded) {
    const expand = el('button', '', 'Expand ↗');
    expand.setAttribute('aria-label', 'Expand code in ' + file.path);
    expand.onclick = () => {
      const repo = state.repositories.find(repo => repo.snapshot.files.includes(file));
      if (repo) {
        const row = rows.find(row => row.now !== null) || rows.find(row => row.old !== null);
        openCodePreview({repositoryId:repo.id, path:file.path, side:row?.now != null ? 'new' : 'old', line:row?.now ?? row?.old ?? 1});
      }
    };
    actions.append(expand);
  }
  toolbar.append(options, actions);
  const code = el('div', 'code'); code.tabIndex = 0;
  code.setAttribute('role', 'region'); code.setAttribute('aria-label', 'Code changes in ' + file.path);
  const update = () => {
    for (const [button, view] of buttons) button.setAttribute('aria-pressed', String(codePreferences.view === view));
    wrap.setAttribute('aria-pressed', String(codePreferences.wrap));
    size.textContent = codePreferences.size + 'px';
    smaller.disabled = codePreferences.size === 11; larger.disabled = codePreferences.size === 18;
    paintCode(code, rows);
  };
  codeViews.set(box, update); update();
  box.append(header, toolbar, code);
  return box;
}

function render() {
  if (!state?.repositories.length) return;
  if (!selectedRepository) selectedRepository = state.repositories.length > 1 ? SYSTEM : state.repositories[0].id;
  const system = selectedRepository === SYSTEM;
  const active = system ? null : state.repositories.find(repo => repo.id === selectedRepository);
  if (!system && !active) { selectedRepository = SYSTEM; return render(); }
  const report = system ? state.overview.report : active.report;
  const snapshot = active?.snapshot;
  const query = $('search').value.toLowerCase(), priority = $('priority').value;
  const matches = value => JSON.stringify(value).toLowerCase().includes(query);
  const picker = $('repository');
  picker.replaceChildren();
  const overview = el('option', '', 'System overview'); overview.value = SYSTEM; picker.append(overview);
  for (const repo of state.repositories) {
    const option = el('option', '', repositoryLabel(repo)); option.value = repo.id; picker.append(option);
  }
  picker.value = selectedRepository;
  $('repositoryPath').textContent = system ? 'All repositories in this session' : active.path;
  $('repositoryPath').title = system ? state.repositories.map(repo => repo.path).join('\n') : active.path;
  $('repo').textContent = system ? state.repositories.length + ' Git snapshots' : 'Base ' + snapshot.base.slice(0,8);
  $('scope').textContent = system ? 'SYSTEM OVERVIEW' : 'REPOSITORY REVIEW';
  $('title').textContent = report?.title || (system ? 'See how the system changes.' : 'A clearer view of your changes.');
  $('summary').textContent = report?.summary || (system
    ? 'Ask your AI to explain behavior across repositories and connect the changes in a system overview.'
    : 'Snapshot captured. Ask your AI to use the Kneeboard skill to add technical understanding and review findings.');
  $('status').textContent = report ? 'AI review · evidence validated' : system ? 'Waiting for system overview' : 'Waiting for AI review';
  $('refresh').textContent = system ? 'Refresh all snapshots' : 'Refresh snapshot';
  $('systemOverview').classList.toggle('active', system);
  $('overview').hidden = system;
  $('overview').classList.toggle('active', !system && !selected);
  $('fileListTitle').textContent = system ? 'PROJECT REPOSITORIES' : 'CHANGED FILES';
  const totalFiles = system ? state.repositories.reduce((sum, repo) => sum + repo.snapshot.files.length, 0) : snapshot.files.length;
  const statistics = [[totalFiles, 'changed files'], [report?.findings.length || 0, system ? 'system findings' : 'review findings'],
    [report?.findings.filter(finding => ['P0','P1'].includes(finding.priority)).length || 0, 'high priorities']];
  if (system) statistics.unshift([state.repositories.length, 'repositories']);
  $('stats').replaceChildren();
  for (const [value, label] of statistics) {
    const node = el('div', 'stat'); node.append(el('b', '', String(value)), el('small', '', label)); $('stats').append(node);
  }
  $('files').replaceChildren();
  if (system) {
    for (const repo of state.repositories) {
      const button = el('button', '', repositoryLabel(repo) + ' · ' + repo.snapshot.files.length + ' files');
      button.onclick = () => switchView(repo.id); $('files').append(button);
    }
  } else {
    for (const file of snapshot.files) {
      const button = el('button', selected === file.path ? 'active' : '', file.path);
      button.onclick = () => {selected = file.path; render();}; $('files').append(button);
    }
  }
  const findings = (report?.findings || []).filter(finding => (!priority || finding.priority === priority)
    && (!selected || finding.evidence.some(entry => entry.path === selected)) && matches(finding))
    .sort((a,b) => a.priority.localeCompare(b.priority));
  $('findingsTitle').textContent = system ? 'System review priorities' : 'Review priorities';
  $('count').textContent = String(findings.length); $('findings').replaceChildren();
  for (const finding of findings) {
    const node = el('article', 'finding');
    node.append(el('span', 'badge', finding.priority + ' · REVIEW'), el('h3', '', finding.title), el('p', '', finding.detail));
    evidenceButtons(node, finding.evidence, system); $('findings').append(node);
  }
  if (!findings.length) $('findings').append(el('div', 'empty', report
    ? 'No findings match this view. An empty review is not a guarantee of correctness.'
    : 'Your AI supplies findings; Kneeboard verifies the evidence and presents them here.'));
  $('diagramTitle').textContent = system ? 'System diagrams' : 'Change map';
  const diagrams = system ? report?.diagrams || [] : report?.diagram ? [report.diagram] : [];
  renderDiagrams(diagrams, system);
  $('diffTitle').textContent = system ? 'System behavior' : 'Code changes';
  $('behaviors').hidden = !system; $('diffs').hidden = system; $('projectRepositories').hidden = !system;
  $('behaviors').replaceChildren(); $('diffs').replaceChildren(); $('repositoryCards').replaceChildren();
  if (system) {
    const behaviors = (report?.behaviors || []).filter(matches);
    for (const behavior of behaviors) {
      const node = el('article', 'panel behavior'); node.append(el('h3', '', behavior.title), el('p', '', behavior.detail));
      evidenceButtons(node, behavior.evidence, true); $('behaviors').append(node);
    }
    if (!behaviors.length) $('behaviors').append(el('div', 'empty', report ? 'No system behaviors match this search.'
      : 'The system overview describes how changes work together across repositories, with links to their code.'));
    for (const repo of state.repositories.filter(matches)) {
      const button = el('button', 'repository-card');
      button.append(el('strong', '', repositoryLabel(repo)), el('span', '', repo.snapshot.files.length + ' changed files · ' + (repo.report ? 'Review available' : 'Awaiting review')));
      button.onclick = () => switchView(repo.id); $('repositoryCards').append(button);
    }
  } else {
    for (const file of snapshot.files.filter(file => (!selected || file.path === selected)
      && (!query || file.path.toLowerCase().includes(query) || file.diff.toLowerCase().includes(query)))) $('diffs').append(renderDiff(file));
  }
}
async function load() {
  try {
    const next = await api('state'), nextSignature = JSON.stringify(next);
    if (ended) return;
    if (nextSignature !== signature) { state = next; signature = nextSignature; render(); if (previewEvidence) updateCodePreview(); }
  } catch (error) { if (!ended) $('error').textContent = error.message; }
}
$('repository').onchange = () => switchView($('repository').value);
$('systemOverview').onclick = () => switchView(SYSTEM);
$('search').oninput = render;
$('priority').onchange = render;
$('overview').onclick = () => { selected = ''; render(); };
$('refresh').onclick = async () => {
  try {
    const target = selectedRepository;
    await api(target === SYSTEM ? 'refresh-all' : 'refresh', target === SYSTEM ? {} : {repositoryId:target});
    if (selectedRepository === target) selected = '';
    await load();
  } catch (error) { $('error').textContent = error.message; }
};
$('close').onclick = async () => {
  try {
    await api('close', {}); closePreview(); ended = true; clearInterval(poll); state = null; signature = ''; diagramFrames = []; diagramSignature = '';
    document.body.replaceChildren(el('p', '', 'Session ended. Review data is saved locally. Reopen the same repositories to continue.'));
  } catch (error) { $('error').textContent = error.message; }
};
function heartbeat() { return api('heartbeat', {clientId}).catch(() => {}); }
window.addEventListener('pagehide', () => {
  if (!ended) navigator.sendBeacon('disconnect', new Blob([JSON.stringify({clientId})], {type:'application/json'}));
});
window.addEventListener('pageshow', heartbeat);
document.addEventListener('visibilitychange', () => { if (!document.hidden && !ended) { heartbeat(); load(); } });
$('connectAI').onclick = () => {
  if (!state) return;
  const panel = $('aiConnection'); panel.hidden = !panel.hidden;
  if (panel.hidden) return;
  const quote = text => "'" + text.replaceAll("'", "'\"'\"'") + "'";
  const command = state.connection.command.map(quote).join(' ');
  const url = new URL('.', location.href).href;
  $('aiPrompt').value = `Use the kneeboard skill to enrich the EXISTING review session at ${url}
Do not start a replacement server, reload or close the report tab, call /close, or stop the server.
Connect with: ${command} connect ${quote(url)}
Keep the returned leaseId. Read the returned state, inspect all listed repositories, and submit a separate report for each repository with submit --repo <id>.
Also submit the system overview with submit --overview using the complete overview.snapshotIds mapping. Include meaningful behavior descriptions and diagrams supported by the code; do not invent findings or evidence.
Verify via GET ${url}state that each intended repository report and the system overview are stored before declaring success. Report submission errors instead of claiming completion.
When done, release only your AI lease with: ${command} finish ${quote(url)} --lease <leaseId>
Leave the session open for me to review.`;
};
$('copyAiPrompt').onclick = async () => {
  try { await navigator.clipboard.writeText($('aiPrompt').value); $('copyStatus').textContent = 'Prompt copied'; }
  catch { $('aiPrompt').focus(); $('aiPrompt').select(); $('copyStatus').textContent = 'Select and copy the prompt above'; }
};
const poll = setInterval(() => { heartbeat(); load(); }, 4000);
heartbeat(); load();
