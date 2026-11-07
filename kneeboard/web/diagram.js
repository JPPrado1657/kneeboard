// Diagram content runs in an opaque-origin sandbox, separate from the report.
let initialized = false, zoom = 1, viewer, expanded = false;
const drawing = document.getElementById('drawing');
const message = document.getElementById('message');
function notify(type, extra = {}) { parent.postMessage({type, ...extra}, '*'); }
function size() {
  notify('kneeboard-diagram-size', {height:Math.min(660, Math.max(160, document.body.scrollHeight))});
}
function loadScript(path) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script'); script.src = path;
    script.onload = resolve; script.onerror = () => reject(Error('Unable to load the bundled renderer'));
    document.head.append(script);
  });
}
async function render(format, source) {
  if (format === 'mermaid') {
    await loadScript('vendor/mermaid.min.js');
    mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:'dark',
      themeVariables:{background:'#161b22', primaryColor:'#142b44', primaryTextColor:'#e6edf3', primaryBorderColor:'#388bfd', lineColor:'#8b9bb0', secondaryColor:'#1c2430', tertiaryColor:'#121922', mainBkg:'#142b44', nodeBorder:'#365675', clusterBkg:'#121922', clusterBorder:'#30363d', edgeLabelBackground:'#161b22'},
      fontFamily:'Arial, sans-serif', flowchart:{htmlLabels:false}, maxTextSize:100000, maxEdges:300,
      suppressErrorRendering:true});
    const {svg} = await mermaid.render('kneeboard-mermaid', source);
    drawing.innerHTML = svg; // Mermaid's strict renderer sanitizes its SVG; isolation remains in force.
    drawing.querySelector('svg').style.maxWidth = 'none';
    drawing.querySelector('svg').style.width = '100%';
  } else if (format === 'drawio') {
    // Keep every upstream resource default local. External libraries and stencils
    // are intentionally not available; the sandbox CSP denies network access.
    const unavailable = new URL('vendor/unavailable/', location.href).href;
    for (const key of ['PROXY_URL','STYLE_PATH','SHAPES_PATH','STENCIL_PATH','DRAW_MATH_URL','GRAPH_IMAGE_PATH','mxImageBasePath','mxBasePath']) window[key] = unavailable;
    window.DRAWIO_BASE_URL = new URL('.',location.href).href;
    window.urlParams = {offline:'1',local:'1',stealth:'1',lockdown:'1',sync:'manual'};
    window.isLocalStorage = false;
    window.onDrawioViewerLoad = () => {};
    await loadScript('vendor/viewer-static.min.js');
    mxStencilRegistry.allowEval = false;
    drawing.className = 'drawio-canvas';
    drawing.setAttribute('data-mxgraph', JSON.stringify({xml:source, nav:true, resize:true, toolbar:'',
      lightbox:false, 'auto-fit':true, 'allow-zoom-in':true, 'check-visible-state':false}));
    GraphViewer.createViewerForElement(drawing, graphViewer => { viewer = graphViewer; });
    if (!drawing.querySelector('svg')) throw Error('No drawable graph found. Use uncompressed XML and built-in shapes.');
  } else if (format === 'svg') {
    const image = document.createElement('img'); image.alt = 'Submitted SVG diagram';
    const url = URL.createObjectURL(new Blob([source], {type:'image/svg+xml'}));
    await new Promise((resolve,reject) => {
      image.onload = resolve; image.onerror = () => reject(Error('Invalid SVG image'));
      image.src = url; drawing.append(image);
    }).finally(() => URL.revokeObjectURL(url));
  } else throw Error('Unsupported diagram format');
  message.textContent = ''; size(); notify('kneeboard-diagram-rendered');
}
window.addEventListener('message', async event => {
  if (event.source !== parent || !event.data || typeof event.data !== 'object') return;
  const data = event.data;
  if (data.type === 'kneeboard-diagram-render' && !initialized) {
    initialized = true;
    expanded = Boolean(data.expanded);
    document.body.classList.toggle('expanded', expanded);
    try { await render(data.format,data.source); }
    catch (error) {
      drawing.replaceChildren(); message.textContent = error.message;
      notify('kneeboard-diagram-error', {message:error.message}); size();
    }
  } else if (data.type === 'kneeboard-diagram-zoom') {
    if (viewer) {
      if (data.direction === 'in') viewer.graph.zoomIn();
      else if (data.direction === 'out') viewer.graph.zoomOut();
      else viewer.graph.fit();
    } else {
      zoom = data.direction === 'reset' ? 1 : Math.max(.5, Math.min(4, zoom + (data.direction === 'in' ? .25 : -.25)));
      drawing.style.width = (zoom * 100) + '%';
    }
    size();
  }
});
window.addEventListener('resize', size);
notify('kneeboard-diagram-ready');

drawing.addEventListener('click', () => { if (!expanded) notify('kneeboard-diagram-expand'); });
