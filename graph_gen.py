"""
graph_gen.py
------------
Generates an interactive D3.js HTML graph of the Obsidian vault.
Usage: python graph_gen.py <vault_path>
Outputs: full HTML to stdout
"""
import sys
import os
import json
import re
from collections import defaultdict


def parse_vault(vault_dir):
    notes = {}
    links = defaultdict(list)
    for root, _, files in os.walk(vault_dir):
        for filename in files:
            if not filename.endswith('.md'):
                continue
            file_path = os.path.join(root, filename)
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                content = ''
            key = os.path.relpath(file_path, vault_dir).lower().replace('\\', '/')
            notes[key] = content
            for match in re.finditer(r'\[\[([^\]|#]+)', content):
                link = match.group(1).strip().lower()
                if not link:
                    continue
                if not link.endswith('.md'):
                    link += '.md'
                links[key].append(link)
    return notes, links


def get_colors(vault_dir):
    try:
        path = os.path.join(vault_dir, '.obsidian', 'graph.json')
        with open(path, 'r') as f:
            cfg = json.load(f)
        groups = cfg.get('colorGroups', [])
        for g in groups:
            rgb = g.get('color', {}).get('rgb', 0)
            g['color'] = f'#{rgb:06x}'
        return groups
    except Exception:
        return []


def build_graph(notes, links, color_groups):
    def node_color(content):
        for g in color_groups:
            q = g.get('query', '')
            if q and re.search(q, content, re.IGNORECASE):
                return g['color']
        return '#6b7280'

    # Build a lookup: bare filename (no path, no ext) → full key
    # so [[Note]] resolves to "note.md" even if stored as "folder/note.md"
    name_to_key = {}
    for key in notes:
        bare = os.path.splitext(os.path.basename(key))[0].lower()
        name_to_key[bare] = key

    def resolve(link):
        """Return the canonical key for a link target, creating ghost nodes if needed."""
        link = link.lower()
        if link in notes:
            return link
        bare = os.path.splitext(os.path.basename(link))[0]
        if bare in name_to_key:
            return name_to_key[bare]
        # dangling link – use the link itself as ghost node key
        return link

    link_count = defaultdict(int)
    edges = []
    seen_edges = set()
    for src, dsts in links.items():
        if src not in notes:
            continue
        for dst_raw in dsts:
            dst = resolve(dst_raw)
            edge_key = (src, dst)
            if edge_key in seen_edges or src == dst:
                continue
            seen_edges.add(edge_key)
            edges.append({'source': src, 'target': dst})
            link_count[src] += 1
            link_count[dst] += 1

    # Collect all node keys (real + ghost)
    all_keys = set(notes.keys()) | {e['target'] for e in edges} | {e['source'] for e in edges}

    nodes = []
    for key in all_keys:
        label = os.path.splitext(key)[0]
        parts = label.replace('\\', '/').split('/')
        label = parts[-1]
        folder = parts[-2] if len(parts) >= 2 else 'root'
        depth  = len(parts) - 1
        content = notes.get(key, '')
        nodes.append({
            'id':         key,
            'label':      label,
            'folder':     folder,
            'depth':      depth,
            'link_count': link_count[key],
            'obsidian_color': node_color(content) if content else '#374151',
        })

    return nodes, edges


def generate_html(nodes, edges, note_count, edge_count, saved_settings=None):
    nodes_json    = json.dumps(nodes)
    edges_json    = json.dumps(edges)
    saved_json    = json.dumps(saved_settings or {})
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ overflow:hidden; background:#0f172a; font-family:'Segoe UI',Arial,sans-serif; color:#e2e8f0; }}
svg {{ width:100vw; height:100vh; display:block; }}
.link {{ stroke-opacity:0.5; }}

/* ── Toolbar ── */
#toolbar {{
  position:fixed; top:0; left:0; right:0; height:44px;
  background:rgba(15,23,42,0.92); backdrop-filter:blur(6px);
  border-bottom:1px solid #1e293b;
  display:flex; align-items:center; gap:8px; padding:0 12px; z-index:20;
}}
#search {{
  background:#1e293b; border:1px solid #334155; color:#e2e8f0;
  padding:5px 11px; border-radius:7px; font-size:13px; width:200px; outline:none;
}}
#search::placeholder {{ color:#475569; }}
.tb-btn {{
  background:#1e293b; border:1px solid #334155; color:#94a3b8;
  padding:5px 11px; border-radius:7px; font-size:12px; cursor:pointer; white-space:nowrap;
}}
.tb-btn:hover {{ background:#334155; color:#e2e8f0; }}
.tb-btn.active {{ background:#3b82f6; border-color:#3b82f6; color:#fff; }}
#info {{ margin-left:auto; color:#475569; font-size:12px; white-space:nowrap; }}

/* ── Settings panel ── */
#settings-panel {{
  position:fixed; top:44px; right:0; bottom:0; width:240px;
  background:rgba(15,23,42,0.97); border-left:1px solid #1e293b;
  padding:14px 14px; overflow-y:auto; z-index:15;
  display:none; flex-direction:column; gap:16px;
}}
#settings-panel.open {{ display:flex; }}
.sp-section {{ display:flex; flex-direction:column; gap:8px; }}
.sp-title {{ font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:.06em; }}
.sp-row {{ display:flex; align-items:center; justify-content:space-between; gap:8px; font-size:13px; }}
.sp-row label {{ color:#94a3b8; flex:1; }}
input[type=range] {{ flex:1; accent-color:#3b82f6; }}
input[type=checkbox] {{ accent-color:#3b82f6; width:15px; height:15px; cursor:pointer; }}
select.sp-sel {{
  background:#1e293b; border:1px solid #334155; color:#e2e8f0;
  padding:4px 8px; border-radius:6px; font-size:12px; cursor:pointer; width:100%;
}}

/* ── Legend ── */
#legend {{
  position:fixed; bottom:12px; left:12px;
  background:rgba(15,23,42,0.88); border:1px solid #1e293b;
  border-radius:8px; padding:8px 12px; font-size:11px; z-index:15;
  display:none; flex-direction:column; gap:4px; max-height:220px; overflow-y:auto;
}}
#legend.open {{ display:flex; }}
.leg-item {{ display:flex; align-items:center; gap:7px; }}
.leg-dot  {{ width:10px; height:10px; border-radius:50%; flex-shrink:0; }}

#tooltip {{
  position:fixed; background:#1e293b; border:1px solid #334155; color:#e2e8f0;
  padding:6px 10px; border-radius:6px; font-size:12px; pointer-events:none;
  display:none; max-width:260px; word-break:break-word; z-index:30;
}}
</style>
</head>
<body>
<div id="toolbar">
  <input id="search" placeholder="🔍 Search note…" />
  <button class="tb-btn" id="btn-labels">Labels ✓</button>
  <button class="tb-btn" id="btn-legend">Legend</button>
  <button class="tb-btn" id="btn-settings">⚙ Settings</button>
  <button class="tb-btn" onclick="location.reload()">↺ Refresh</button>
  <span id="info">{note_count} notes · {edge_count} links</span>
</div>

<div id="graph"></div>
<div id="tooltip"></div>

<!-- Settings panel -->
<div id="settings-panel">
  <div class="sp-section">
    <div class="sp-title">Color mode</div>
    <select class="sp-sel" id="sel-color">
      <option value="obsidian">Obsidian tags</option>
      <option value="folder" selected>Folder / hierarchy</option>
      <option value="depth">Depth (dark→light)</option>
      <option value="links">Link count (heat)</option>
      <option value="flat">Flat (single color)</option>
    </select>
  </div>
  <div class="sp-section">
    <div class="sp-title">Node size</div>
    <div class="sp-row"><label>Base radius</label><input type="range" id="sl-node-base" min="2" max="14" value="4" step="1"><span id="v-node-base">4</span></div>
    <div class="sp-row"><label>Scale by links</label><input type="range" id="sl-node-scale" min="0" max="3" value="1" step="0.1"><span id="v-node-scale">1</span></div>
  </div>
  <div class="sp-section">
    <div class="sp-title">Links</div>
    <div class="sp-row"><label>Opacity</label><input type="range" id="sl-link-opacity" min="0.05" max="1" value="0.5" step="0.05"><span id="v-link-opacity">0.5</span></div>
    <div class="sp-row"><label>Thickness</label><input type="range" id="sl-link-width" min="0.5" max="4" value="1" step="0.5"><span id="v-link-width">1</span></div>
    <div class="sp-row"><label>Color</label>
      <select class="sp-sel" id="sel-link-color">
        <option value="#334155">Dark blue</option>
        <option value="#6b7280">Gray</option>
        <option value="#7c3aed">Purple</option>
        <option value="#0f766e">Teal</option>
      </select>
    </div>
  </div>
  <div class="sp-section">
    <div class="sp-title">Labels</div>
    <div class="sp-row"><label>Font size</label><input type="range" id="sl-font" min="8" max="18" value="11" step="1"><span id="v-font">11</span></div>
    <div class="sp-row"><label>Show always</label><input type="checkbox" id="chk-labels" checked></div>
    <div class="sp-row"><label>Show on hover only</label><input type="checkbox" id="chk-hover"></div>
  </div>
  <div class="sp-section">
    <div class="sp-title">Physics</div>
    <div class="sp-row"><label>Repulsion</label><input type="range" id="sl-charge" min="-1200" max="-50" value="-500" step="50"><span id="v-charge">-500</span></div>
    <div class="sp-row"><label>Link distance</label><input type="range" id="sl-dist" min="20" max="250" value="55" step="5"><span id="v-dist">55</span></div>
  </div>
  <div class="sp-section">
    <div class="sp-title">Display</div>
    <div class="sp-row"><label>Hide ghost nodes</label><input type="checkbox" id="chk-ghost"></div>
    <div class="sp-row"><label>Background</label>
      <select class="sp-sel" id="sel-bg">
        <option value="#0f172a" selected>Dark navy</option>
        <option value="#1a1a2e">Deep dark</option>
        <option value="#111111">Black</option>
        <option value="#1e1e2e">Catppuccin</option>
        <option value="#0d1117">GitHub dark</option>
      </select>
    </div>
  </div>
</div>

<!-- Legend -->
<div id="legend"></div>

<script src="https://d3js.org/d3.v5.min.js"></script>
<script>
var nodes = {nodes_json};
var links = {edges_json};

// ── Folder color palette ──
var folders = Array.from(new Set(nodes.map(function(d){{ return d.folder; }})));
var folderPalette = [
  '#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
  '#06b6d4','#f97316','#ec4899','#84cc16','#14b8a6',
  '#a78bfa','#fb923c','#34d399','#60a5fa','#fbbf24'
];
var folderColor = {{}};
folders.forEach(function(f,i){{ folderColor[f] = folderPalette[i % folderPalette.length]; }});

// depth color scale
var depthMax = d3.max(nodes, function(d){{ return d.depth || 0; }}) || 1;
var depthScale = d3.scaleSequential(d3.interpolateCool).domain([0, depthMax]);

// link-count heat scale
var linkMax = d3.max(nodes, function(d){{ return d.link_count || 0; }}) || 1;
var heatScale = d3.scaleSequential(d3.interpolateYlOrRd).domain([0, linkMax]);

var settings = {{
  colorMode: 'folder',
  nodeBase: 4, nodeScale: 1,
  linkOpacity: 0.5, linkWidth: 1, linkColor: '#334155',
  fontSize: 11,
  showLabels: true, hoverLabels: false,
  charge: -500, linkDist: 55,
  hideGhost: false,
}};
// Merge saved settings from settings.json
(function(){{
  var saved = {saved_json};
  Object.keys(saved).forEach(function(k){{ settings[k] = saved[k]; }});
}})();

function persistSettings(){{
  window.parent.postMessage({{type:'save-graph-settings', settings: Object.assign({{}}, settings)}}, '*');
}}

function nodeColor(d) {{
  if (settings.hideGhost && !d.obsidian_color) return null; // ghost
  switch(settings.colorMode) {{
    case 'obsidian': return d.obsidian_color || '#374151';
    case 'folder':   return folderColor[d.folder] || '#6b7280';
    case 'depth':    return depthScale(d.depth || 0);
    case 'links':    return heatScale(d.link_count || 0);
    case 'flat':     return '#3b82f6';
  }}
  return '#6b7280';
}}

function nodeR(d) {{
  return settings.nodeBase + Math.sqrt(d.link_count || 0) * settings.nodeScale;
}}

// ── SVG setup ──
var w = window.innerWidth, h = window.innerHeight;
var svg = d3.select("#graph").append("svg").attr("width", w).attr("height", h);
var g   = svg.append("g");

svg.call(d3.zoom().scaleExtent([0.03,12]).on("zoom", function(){{
  g.attr("transform", d3.event.transform);
}}));

var spread = Math.min(w,h)*0.45;
nodes.forEach(function(d,i){{
  var angle = (i/nodes.length)*2*Math.PI;
  var r = spread*(0.2+Math.random()*0.8);
  d.x = w/2+r*Math.cos(angle);
  d.y = h/2+r*Math.sin(angle);
}});

var sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(function(d){{return d.id;}})
    .distance(function(d){{ return settings.linkDist + Math.sqrt((d.source.link_count||1)+(d.target.link_count||1))*10; }})
    .strength(0.5))
  .force("charge", d3.forceManyBody().strength(function(d){{ return settings.charge - (d.link_count||0)*30; }}).distanceMax(600).distanceMin(20))
  .force("x", d3.forceX(w/2).strength(0.03))
  .force("y", d3.forceY(h/2).strength(0.03))
  .force("collision", d3.forceCollide(function(d){{ return nodeR(d)+8; }}))
  .alphaDecay(0.010).velocityDecay(0.4);

var linkEl = g.append("g").selectAll("line")
  .data(links).enter().append("line").attr("class","link")
  .attr("stroke", settings.linkColor)
  .attr("stroke-width", settings.linkWidth)
  .attr("stroke-opacity", settings.linkOpacity);

var nodeEl = g.append("g").selectAll("circle")
  .data(nodes).enter().append("circle")
  .attr("r", nodeR)
  .attr("fill", nodeColor)
  .attr("stroke","#0f172a").attr("stroke-width",1)
  .style("cursor","move")
  .call(d3.drag()
    .on("start",function(d){{ if(!d3.event.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
    .on("drag", function(d){{ d.fx=d3.event.x; d.fy=d3.event.y; }})
    .on("end",  function(d){{ if(!d3.event.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}))
  .on("mouseover", function(d){{
    var tt=document.getElementById("tooltip");
    tt.style.display="block";
    tt.innerHTML="<b>"+d.label+"</b><br>Folder: "+d.folder+"<br>Links: "+(d.link_count||0)+"<br>Depth: "+(d.depth||0);
    if(settings.hoverLabels) d3.select(textEl.nodes()[nodes.indexOf(d)]).attr("display","block");
  }})
  .on("mousemove",function(){{
    var tt=document.getElementById("tooltip");
    tt.style.left=(d3.event.pageX+14)+"px"; tt.style.top=(d3.event.pageY-10)+"px";
  }})
  .on("mouseout",function(){{
    document.getElementById("tooltip").style.display="none";
  }});

var textEl = g.append("g").selectAll("text")
  .data(nodes).enter().append("text")
  .attr("font-size", settings.fontSize)
  .attr("x",8).attr("y",".31em")
  .attr("fill","#94a3b8")
  .text(function(d){{ return d.label; }});

sim.on("tick",function(){{
  linkEl.attr("x1",function(d){{return d.source.x;}}).attr("y1",function(d){{return d.source.y;}})
        .attr("x2",function(d){{return d.target.x;}}).attr("y2",function(d){{return d.target.y;}});
  nodeEl.attr("cx",function(d){{return d.x;}}).attr("cy",function(d){{return d.y;}});
  textEl.attr("x",function(d){{return d.x+nodeR(d)+3;}}).attr("y",function(d){{return d.y+3;}});
}});

// ── Apply all settings to DOM ──
function applySettings(){{
  nodeEl.attr("fill", nodeColor).attr("r", nodeR);
  nodeEl.attr("display", function(d){{ return (settings.hideGhost && !d.obsidian_color) ? "none" : "block"; }});
  linkEl.attr("stroke", settings.linkColor)
        .attr("stroke-width", settings.linkWidth)
        .attr("stroke-opacity", settings.linkOpacity);
  textEl.attr("font-size", settings.fontSize)
        .attr("display", function(){{ return settings.showLabels && !settings.hoverLabels ? "block" : "none"; }});
  sim.force("collision", d3.forceCollide(function(d){{ return nodeR(d)+8; }}));
  sim.force("charge", d3.forceManyBody().strength(function(d){{ return settings.charge-(d.link_count||0)*30; }}).distanceMax(600).distanceMin(20));
  sim.force("link").distance(function(d){{ return settings.linkDist+Math.sqrt((d.source.link_count||1)+(d.target.link_count||1))*10; }});
  sim.alpha(0.3).restart();
  buildLegend();
  document.body.style.background = document.getElementById("sel-bg").value;
  persistSettings();
}}

// ── Legend ──
function buildLegend(){{
  var leg = document.getElementById("legend");
  leg.innerHTML="";
  var items = [];
  if(settings.colorMode==="folder"){{
    folders.forEach(function(f){{ items.push({{color:folderColor[f],label:f}}); }});
  }} else if(settings.colorMode==="flat"){{
    items.push({{color:"#3b82f6",label:"All nodes"}});
  }} else if(settings.colorMode==="obsidian"){{
    items.push({{color:"#374151",label:"(ghost)"}});
    items.push({{color:"#6b7280",label:"default"}});
  }}
  items.forEach(function(it){{
    var row=document.createElement("div"); row.className="leg-item";
    var dot=document.createElement("div"); dot.className="leg-dot"; dot.style.background=it.color;
    var lbl=document.createElement("span"); lbl.textContent=it.label; lbl.style.color="#94a3b8";
    row.appendChild(dot); row.appendChild(lbl); leg.appendChild(row);
  }});
}}

// ── Search ──
document.getElementById("search").addEventListener("input",function(){{
  var q=this.value.trim().toLowerCase();
  nodeEl.attr("opacity",function(d){{ return !q||d.label.toLowerCase().includes(q)?1:0.06; }});
  textEl.attr("opacity",function(d){{ return !q||d.label.toLowerCase().includes(q)?1:0.04; }});
  linkEl.attr("opacity",function(d){{
    if(!q) return settings.linkOpacity;
    return ((d.source.label||"").toLowerCase().includes(q)||(d.target.label||"").toLowerCase().includes(q))?settings.linkOpacity:0.03;
  }});
}});

// ── Toolbar buttons ──
var labelsOn = true;
document.getElementById("btn-labels").addEventListener("click",function(){{
  labelsOn=!labelsOn;
  settings.showLabels=labelsOn;
  this.textContent=labelsOn?"Labels ✓":"Labels ✗";
  this.classList.toggle("active", labelsOn);
  applySettings();
}});
document.getElementById("btn-settings").addEventListener("click",function(){{
  var p=document.getElementById("settings-panel");
  p.classList.toggle("open");
  this.classList.toggle("active", p.classList.contains("open"));
}});
document.getElementById("btn-legend").addEventListener("click",function(){{
  var l=document.getElementById("legend");
  l.classList.toggle("open");
  this.classList.toggle("active", l.classList.contains("open"));
}});

// ── Settings controls ──
function bindRange(id,valId,key,transform){{
  var el=document.getElementById(id), vEl=document.getElementById(valId);
  el.addEventListener("input",function(){{
    var v=parseFloat(this.value);
    settings[key]=transform?transform(v):v;
    vEl.textContent=this.value;
    applySettings();
  }});
}}
function bindCheck(id,key){{
  document.getElementById(id).addEventListener("change",function(){{
    settings[key]=this.checked; applySettings();
  }});
}}
bindRange("sl-node-base","v-node-base","nodeBase");
bindRange("sl-node-scale","v-node-scale","nodeScale");
bindRange("sl-link-opacity","v-link-opacity","linkOpacity");
bindRange("sl-link-width","v-link-width","linkWidth");
bindRange("sl-font","v-font","fontSize");
bindRange("sl-charge","v-charge","charge");
bindRange("sl-dist","v-dist","linkDist");
bindCheck("chk-labels","showLabels");
bindCheck("chk-hover","hoverLabels");
bindCheck("chk-ghost","hideGhost");

document.getElementById("sel-color").addEventListener("change",function(){{
  settings.colorMode=this.value; applySettings();
}});
document.getElementById("sel-link-color").addEventListener("change",function(){{
  settings.linkColor=this.value; applySettings();
}});
document.getElementById("sel-bg").addEventListener("change",function(){{
  document.body.style.background=this.value;
}});

// ── Init: sync controls to loaded settings ──
(function(){{
  function setVal(id, val){{ var el=document.getElementById(id); if(el) el.value=val; }}
  function setChk(id, val){{ var el=document.getElementById(id); if(el) el.checked=val; }}
  function setSpan(id, val){{ var el=document.getElementById(id); if(el) el.textContent=val; }}
  setVal("sl-node-base",      settings.nodeBase);      setSpan("v-node-base",      settings.nodeBase);
  setVal("sl-node-scale",     settings.nodeScale);     setSpan("v-node-scale",     settings.nodeScale);
  setVal("sl-link-opacity",   settings.linkOpacity);   setSpan("v-link-opacity",   settings.linkOpacity);
  setVal("sl-link-width",     settings.linkWidth);     setSpan("v-link-width",     settings.linkWidth);
  setVal("sl-font",           settings.fontSize);      setSpan("v-font",           settings.fontSize);
  setVal("sl-charge",         settings.charge);        setSpan("v-charge",         settings.charge);
  setVal("sl-dist",           settings.linkDist);      setSpan("v-dist",           settings.linkDist);
  setVal("sel-color",         settings.colorMode);
  setVal("sel-link-color",    settings.linkColor);
  setChk("chk-labels",        settings.showLabels);
  setChk("chk-hover",         settings.hoverLabels);
  setChk("chk-ghost",         settings.hideGhost);
  var labelsBtn=document.getElementById("btn-labels");
  if(labelsBtn) {{ labelsBtn.textContent=settings.showLabels?"Labels ✓":"Labels ✗"; labelsBtn.classList.toggle("active",settings.showLabels); }}
}})();
applySettings();
</script>
</body>
</html>"""


if __name__ == '__main__':
    vault = sys.argv[1] if len(sys.argv) > 1 else '.'
    saved_settings = {}
    if len(sys.argv) > 2:
        try:
            saved_settings = json.loads(sys.argv[2])
        except Exception:
            saved_settings = {}
    notes, links = parse_vault(vault)
    colors       = get_colors(vault)
    nodes, edges = build_graph(notes, links, colors)
    html         = generate_html(nodes, edges, len(notes), len(edges), saved_settings)
    sys.stdout.buffer.write(html.encode('utf-8'))
