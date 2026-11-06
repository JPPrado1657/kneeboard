let state, selected = '', selectedRepository = '', signature = '', ended = false;
const SYSTEM = 'system';
const clientId = crypto.randomUUID();
let diagramSignature = '', diagramFrames = [];
window.addEventListener('message', event => {
  const entry = diagramFrames.find(item => item.frame.contentWindow === event.source);
  if (!entry || !event.data || typeof event.data !== 'object') return;
  if (event.data.type === 'kneeboard-diagram-ready') {
    entry.frame.contentWindow.postMessage({type:'kneeboard-diagram-render', format:entry.diagram.format, source:entry.diagram.source}, '*');
  } else if (event.data.type === 'kneeboard-diagram-size') {
    const height = Number(event.data.height);
    if (Number.isFinite(height)) entry.frame.height = Math.max(160, Math.min(660, height));
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
function jump(evidence) {
  if (evidence.repositoryId) selectedRepository = evidence.repositoryId;
  selected = evidence.path;
  $('search').value = '';
  $('priority').value = '';
  render();
  const attribute = evidence.side === 'old' ? 'old-line' : 'new-line';
  const line = document.querySelector(`[data-${attribute}="${evidence.line}"]`);
  if (line) {
    line.classList.add('focus');
    line.scrollIntoView({behavior:'smooth', block:'center'});
  }
}
function evidenceButtons(container, evidence, system) {
  for (const entry of evidence) {
    const repo = state.repositories.find(item => item.id === entry.repositoryId);
    const prefix = system && repo ? repositoryLabel(repo) + ' / ' : '';
    const button = el('button', 'evidence', `${prefix}${entry.path}:${entry.line} (${entry.side}) ↗\n${entry.quote}`);
    button.onclick = () => jump(entry);
    container.append(button);
  }
}
function renderDiagram(diagram, system) {
  const panel = el('div', 'panel diagram');
  const format = diagram.format || 'graph';
  if (system) panel.append(el('h3', '', diagram.title));
  if (format === 'graph') {
    for (const node of diagram.nodes || []) panel.append(el('span', 'node', node));
    for (const [from, to] of diagram.edges || []) panel.append(el('div', 'edge', from + ' → ' + to));
    return panel;
  }
  const toolbar = el('div', 'diagram-toolbar');
  toolbar.append(el('span', 'badge', format === 'drawio' ? 'DRAW.IO' : format.toUpperCase()));
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
  diagramFrames.push({frame, diagram, status});
  const details = el('details', 'diagram-source');
  details.append(el('summary', '', 'View ' + (format === 'drawio' ? 'draw.io XML' : format) + ' source'), el('pre', '', diagram.source));
  panel.append(toolbar, frame, status, details);
  return panel;
}
function renderDiagrams(diagrams, system) {
  const nextSignature = JSON.stringify([selectedRepository, diagrams]);
  if (nextSignature === diagramSignature) return;
  diagramSignature = nextSignature; diagramFrames = [];
  $('diagram').replaceChildren();
  for (const diagram of diagrams) $('diagram').append(renderDiagram(diagram, system));
  if (!diagrams.length) $('diagram').append(el('div', 'empty', system
    ? 'Your AI can supply architecture, request-flow, and behavior diagrams spanning the project.'
    : 'Your AI can supply a change map with labeled components and connections.'));
}
function renderDiff(file) {
  const box = el('article', 'diff');
  box.append(el('h3', '', file.path + ' · ' + file.status));
  const code = el('div', 'code');
  let old = 0, now = 0;
  for (const line of (file.omitted || file.diff || 'No textual diff available').split('\n')) {
    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
    if (hunk) { old = +hunk[1]; now = +hunk[2]; }
    const header = line.startsWith('--- ') || line.startsWith('+++ ');
    const node = el('div', 'line' + (hunk ? ' hunk' : !header && line.startsWith('+') ? ' add' : !header && line.startsWith('-') ? ' del' : ''), line);
    if (!hunk && !header) {
      if (line.startsWith('+')) node.dataset.newLine = now++;
      else if (line.startsWith('-')) node.dataset.oldLine = old++;
      else if (line.startsWith(' ')) { node.dataset.newLine = now++; node.dataset.oldLine = old++; }
    }
    code.append(node);
  }
  box.append(code);
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
    if (nextSignature !== signature) { state = next; signature = nextSignature; render(); }
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
    await api('close', {}); ended = true; clearInterval(poll); state = null; signature = ''; diagramFrames = []; diagramSignature = '';
    document.body.replaceChildren(el('p', '', 'Session ended. Review data has been removed from the running application.'));
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
