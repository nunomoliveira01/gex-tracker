/* Blocks editor: the tracker's settings as snap-together blocks.
   Shared by the website (docs/live.html, saves straight to GitHub) and the Claude artifact copy (saves to the
   artifact's database; Claude applies it to GitHub when asked). Usage:
     BlocksEditor(rootEl, { load: async () => ({ config, watchlist }), save: async ({ config, watchlist }) => "message",
                            panels: [[blockId, name, [[cardId, name], ...], elementId], ...] (optional), lines: [[key, name], ...] (optional) }) */
(function () {
  const ENTRY = {
    put_wall: { t: "SPY falls to the day's put wall", side: "long" },
    call_wall: { t: "SPY rises to the day's call wall", side: "short" },
    flip_down: { t: "SPY falls through the gamma flip", side: "short" },
    flip_up: { t: "SPY rises through the gamma flip", side: "long" },
    gamma_below: { t: "SPY gamma at or below", p: "bn", d: -10, u: "bn", side: "long" },
    gamma_above: { t: "SPY gamma at or above", p: "bn", d: 0, u: "bn", side: "short" },
  };
  const FILTER = { gamma_negative: { t: "only if SPY gamma is negative" }, gamma_positive: { t: "only if SPY gamma is positive" } };
  const EXIT = {
    opposite_wall: { t: "SPY reaches the opposite wall" },
    flip: { t: "SPY gamma back at 0 (the flip)" },
    gamma_level: { t: "SPY gamma reaches", p: "bn", d: 0, u: "bn" },
    target_pct: { t: "Take profit at", p: "pct", d: 1, u: "% SPY move" },
    stop_pct: { t: "Stop at", p: "pct", d: 1, u: "% SPY move against" },
    days: { t: "After", p: "n", d: 5, u: "trading days" },
  };
  const TRADES = ["NQ", "ES", "QQQ", "SPY"];
  const PRESETS = {
    "Put wall long": { side: "long", entry: { type: "put_wall" }, filters: [], exits: [{ type: "opposite_wall" }, { type: "flip" }] },
    "Call wall short": { side: "short", entry: { type: "call_wall" }, filters: [], exits: [{ type: "opposite_wall" }, { type: "flip" }] },
    "Gamma −10bn long": { side: "long", entry: { type: "gamma_below", bn: -10 }, filters: [], exits: [{ type: "flip" }] },
    "Empty": { side: "long", entry: { type: "put_wall" }, filters: [], exits: [{ type: "flip" }] },
  };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = o => JSON.parse(JSON.stringify(o));

  function describe(s) {
    const long = s.side === "long", e = s.entry || {};
    const ent = { put_wall: "SPY falls to the day's put wall", call_wall: "SPY rises to the day's call wall", flip_down: "SPY falls through the gamma flip",
      flip_up: "SPY rises through the gamma flip", gamma_below: `SPY dealer gamma is at ${e.bn}bn or lower`, gamma_above: `SPY dealer gamma is at ${e.bn}bn or higher` }[e.type] || e.type;
    const ex = (s.exits || []).map(x => ({ opposite_wall: "SPY reaches the day's " + (long ? "call wall" : "put wall"), flip: "SPY gamma is back at 0 (the flip)",
      gamma_level: `SPY gamma reaches ${x.bn}bn`, target_pct: `SPY moves ${x.pct}% in your favour`, stop_pct: `SPY moves ${x.pct}% against you`,
      days: `${x.n} trading days have passed` }[x.type] || x.type));
    const f = (s.filters || []).map(x => ", " + (FILTER[x.type] || { t: x.type }).t).join("");
    const a = s.add_on || {};
    return `${long ? "Buy" : "Sell short"} 1 ${s.trade || "NQ"} when ${ent}${f}. From the next day, close ${ex.length > 1 ? "at whichever comes first: " : "when "}${ex.join("; ") || "— add an exit block"}.` +
      (a.on ? ` Add 1 unit once at a later close if SPY is ${long ? "below" : "above"} the entry and the put/call ratio is ${a.z ?? 1} std ${long ? "above" : "below"} normal.` : "");
  }

  const STYLE = `
  .bk{--bk-ink:var(--ink,#1b2240);--bk-mut:var(--muted,#6b7393);--bk-line:var(--line,#e2e6f0);--bk-card:var(--card,#fff);--bk-soft:var(--soft,#f2f4fa);
      color:var(--bk-ink);font-size:15px;line-height:1.45}
  .bk *{box-sizing:border-box;min-width:0}
  .bk-top{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
  .bk-tabs{display:flex;flex-wrap:wrap;gap:4px;flex:1}
  .bk-tab{border:1px solid var(--bk-line);background:var(--bk-card);border-radius:999px;padding:7px 14px;cursor:pointer;font-weight:700;min-height:40px}
  .bk-tab[aria-selected=true]{background:var(--bk-ink);color:var(--bk-card);border-color:var(--bk-ink)}
  .bk-btn{border:1px solid var(--bk-line);background:var(--bk-card);border-radius:10px;padding:8px 14px;cursor:pointer;font-weight:700;min-height:40px}
  .bk-btn.pri{background:#2f6fed;border-color:#2f6fed;color:#fff}.bk-btn.dan{color:#d33}
  .bk-btn:focus-visible,.bk-tab:focus-visible,.bk input:focus-visible,.bk select:focus-visible{outline:3px solid #2f6fed66;outline-offset:1px}
  .bk-msg{font-size:13px;color:var(--bk-mut);width:100%}
  .bk-grid{display:grid;grid-template-columns:220px minmax(0,1fr);gap:14px}
  @media(max-width:760px){.bk-grid{grid-template-columns:minmax(0,1fr)}.bk-pal{position:static!important}.bk-pal .dk{display:none}}
  .bk-pal{position:sticky;top:8px;align-self:start;border:1px solid var(--bk-line);border-radius:14px;padding:10px;background:var(--bk-soft)}
  .bk-pal h5{margin:8px 0 4px;font-size:12px;color:var(--bk-mut);font-weight:800}
  .bk-chip{display:block;width:100%;text-align:left;border:0;border-radius:9px;padding:7px 10px;margin:4px 0;font-weight:700;font-size:13px;cursor:grab;color:#fff}
  .k-entry{background:#e0811a}.k-filter{background:#8a5cf6}.k-exit{background:#1f9d6b}.k-do{background:#2f6fed}.k-add{background:#c2417f}.k-misc{background:#5b6478}
  .bk-strat{border:1px solid var(--bk-line);border-radius:16px;background:var(--bk-card);padding:12px;margin-bottom:14px}
  .bk-strat.off{opacity:.55}
  .bk-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}
  .bk-head input.name{font-weight:800;font-size:17px;border:1px solid transparent;background:transparent;border-radius:8px;padding:4px 6px;flex:1 1 180px}
  .bk-head input.name:hover,.bk-head input.name:focus{border-color:var(--bk-line)}
  .bk-row{display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px;align-items:start;margin:6px 0}
  @media(max-width:520px){.bk-row{grid-template-columns:minmax(0,1fr)}}
  .bk-lab{font-size:12px;font-weight:800;color:var(--bk-mut);padding-top:9px}
  .bk-slot{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:42px;border:2px dashed transparent;border-radius:12px;padding:2px}
  .bk-slot.over{border-color:#2f6fed;background:#2f6fed14}
  .bk-blk{display:inline-flex;flex-wrap:wrap;align-items:center;gap:6px;color:#fff;border-radius:10px;padding:6px 8px 6px 10px;font-weight:700;font-size:13px;position:relative;max-width:100%}
  .bk-blk::before{content:"";position:absolute;left:14px;top:-5px;width:14px;height:6px;border-radius:3px 3px 0 0;background:inherit}
  .bk-blk input,.bk-blk select{border:0;border-radius:7px;padding:4px 6px;font:inherit;font-size:14px;background:#ffffffe6;color:#1b2240;min-height:30px}
  .bk-blk input[type=number]{width:76px}
  .bk-x{border:0;background:#0003;color:#fff;border-radius:7px;width:26px;height:26px;cursor:pointer;font-weight:800}
  .bk-add{border:1px dashed var(--bk-line);background:transparent;border-radius:10px;padding:5px 8px;font-size:13px;color:var(--bk-mut);min-height:34px}
  .bk-say{font-size:13px;color:var(--bk-mut);background:var(--bk-soft);border-radius:10px;padding:8px 10px;margin-top:8px}
  .bk-sw{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:700;cursor:pointer}
  .bk-card{border:1px solid var(--bk-line);border-radius:14px;background:var(--bk-card);padding:12px;margin-bottom:12px}
  .bk-card h4{margin:0 0 8px}
  .bk-f{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}
  .bk-f input,.bk-f select{border:1px solid var(--bk-line);border-radius:8px;padding:6px 8px;font-size:15px;background:var(--bk-card);color:var(--bk-ink);min-height:38px}
  .bk-tag{display:inline-flex;gap:4px;align-items:center;background:var(--bk-soft);border:1px solid var(--bk-line);border-radius:999px;padding:4px 6px 4px 10px;font-weight:700}
  .bk-tag button{border:0;background:transparent;cursor:pointer;color:var(--bk-mut);font-weight:800}
  .bk-lay{list-style:none;margin:0;padding:0}
  .bk-lay li{display:flex;flex-wrap:wrap;gap:8px;align-items:center;border:1px solid var(--bk-line);border-radius:10px;padding:6px 8px;margin:5px 0;background:var(--bk-card)}
  .bk-lay li b{flex:1 1 160px}
  .bk-kids{width:100%;display:flex;flex-wrap:wrap;gap:4px 12px;padding-left:28px;font-size:13px}
  .bk-dirty{color:#d48a00;font-weight:800}`;

  window.BlocksEditor = function (root, opts) {
    if (!document.getElementById("bk-css")) { const st = document.createElement("style"); st.id = "bk-css"; st.textContent = STYLE; document.head.appendChild(st); }
    let S = null, saved = "", tab = "strategies", busy = false, note = "";
    const dirty = () => S && JSON.stringify(S) !== saved;
    const get = (path) => path.split(".").reduce((o, k) => o == null ? o : o[k], S);
    const set = (path, v) => { const ks = path.split("."), last = ks.pop(); const o = ks.reduce((o, k) => (o[k] ??= /^\d+$/.test(k) ? [] : {}), S); o[last] = v; };
    const strats = () => (S.config.strategies ??= []);

    function blk(kind, defs, item, path, idx) {
      const d = defs[item.type] || { t: item.type };
      const field = d.p ? ` <input type="number" step="any" value="${esc(item[d.p] ?? d.d)}" data-bind="${path}.${d.p}" data-num aria-label="${esc(d.t)}"> ${esc(d.u || "")}` : "";
      const sel = kind === "k-entry" ? `<select data-bind="${path}.type" data-retype aria-label="Entry">${Object.entries(defs).map(([k, v]) => `<option value="${k}" ${k === item.type ? "selected" : ""}>${esc(v.t)}</option>`).join("")}</select>` : esc(d.t);
      const x = idx != null ? `<button class="bk-x" data-act="rm" data-path="${path}" title="Remove this block">×</button>` : "";
      return `<span class="bk-blk ${kind}">${sel}${field}${x}</span>`;
    }
    function addSel(sid, list, defs, label) {
      return `<select class="bk-add" data-act="addsel" data-sid="${sid}" data-list="${list}" aria-label="${label}"><option value="">+ ${label}</option>${Object.entries(defs).map(([k, v]) => `<option value="${k}">${esc(v.t)}</option>`).join("")}</select>`;
    }
    function stratHTML(s, i) {
      const p = `config.strategies.${i}`, fixed = !!s.signal_file;
      const exits = (s.exits || []).map((x, j) => blk("k-exit", EXIT, x, `${p}.exits.${j}`, j)).join("");
      const filters = (s.filters || []).map((x, j) => blk("k-filter", FILTER, x, `${p}.filters.${j}`, j)).join("");
      const a = s.add_on || {};
      return `<div class="bk-strat ${s.on === false ? "off" : ""}" data-sid="${i}">
        <div class="bk-head"><input class="name" value="${esc(s.name)}" data-bind="${p}.name" aria-label="Strategy name">
          <label class="bk-sw"><input type="checkbox" data-bind="${p}.on" data-bool ${s.on !== false ? "checked" : ""}> tracking on</label>
          <button class="bk-btn" data-act="dup" data-sid="${i}">Duplicate</button>
          ${fixed ? "" : `<button class="bk-btn dan" data-act="del" data-sid="${i}">Delete</button>`}</div>
        <div class="bk-row"><div class="bk-lab">WHEN</div><div class="bk-slot" data-drop="entry" data-sid="${i}">${blk("k-entry", ENTRY, s.entry || { type: "put_wall" }, `${p}.entry`)}</div></div>
        <div class="bk-row"><div class="bk-lab">ONLY IF</div><div class="bk-slot" data-drop="filters" data-sid="${i}">${filters}${addSel(i, "filters", FILTER, "filter")}</div></div>
        <div class="bk-row"><div class="bk-lab">DO</div><div class="bk-slot"><span class="bk-blk k-do">
          <select data-bind="${p}.side" aria-label="Buy or sell"><option value="long" ${s.side === "long" ? "selected" : ""}>Buy (long)</option><option value="short" ${s.side === "short" ? "selected" : ""}>Sell short</option></select>
          1 unit of <select data-bind="${p}.trade" aria-label="Instrument">${TRADES.map(t => `<option ${t === (s.trade || "NQ") ? "selected" : ""}>${t}</option>`).join("")}</select></span></div></div>
        <div class="bk-row"><div class="bk-lab">EXIT<br><span style="font-weight:600">first one wins</span></div><div class="bk-slot" data-drop="exits" data-sid="${i}">${exits}${addSel(i, "exits", EXIT, "exit")}</div></div>
        <div class="bk-row"><div class="bk-lab">ADD-ON</div><div class="bk-slot"><span class="bk-blk k-add"><label class="bk-sw"><input type="checkbox" data-bind="${p}.add_on.on" data-bool ${a.on ? "checked" : ""}> add 1 unit once</label>
          ${a.on ? `when put/call is <input type="number" step="0.1" value="${esc(a.z ?? 1)}" data-bind="${p}.add_on.z" data-num aria-label="Put/call std"> std from normal` : ""}</span></div></div>
        <div class="bk-row"><div class="bk-lab">LIVE FROM</div><div class="bk-slot"><span class="bk-blk k-misc"><input type="date" value="${esc((s.start || "").slice(0, 10))}" data-bind="${p}.start" data-date aria-label="Live record start"></span>
          ${fixed ? `<span class="bk-say" style="margin:0">has its own panel on the site</span>` : `<label class="bk-sw"><input type="checkbox" data-bind="${p}.panel" data-bool ${s.panel !== false ? "checked" : ""}> own panel on the site</label>`}</div></div>
        <div class="bk-say">${esc(describe(s))}</div></div>`;
    }
    function palette() {
      const chips = (kind, defs, dk) => Object.entries(defs).map(([k, v]) => `<button class="bk-chip ${kind}" draggable="true" data-dk="${dk}" data-dt="${k}" title="Drag into a strategy">${esc(v.t)}${v.p ? " …" : ""}</button>`).join("");
      return `<div class="bk-pal"><div style="font-weight:800">Blocks</div><div class="dk"><div style="font-size:12px;color:var(--bk-mut)">Drag a block into a strategy, or use the + menus.</div>
        <h5>WHEN (entry)</h5>${chips("k-entry", ENTRY, "entry")}<h5>ONLY IF</h5>${chips("k-filter", FILTER, "filters")}<h5>EXIT</h5>${chips("k-exit", EXIT, "exits")}</div>
        <h5>NEW STRATEGY</h5>${Object.keys(PRESETS).map(n => `<button class="bk-chip k-do" data-act="new" data-preset="${esc(n)}">+ ${esc(n)}</button>`).join("")}</div>`;
    }
    function mineHTML() {
      const c = S.config;
      return `<div class="bk-card"><h4>My strategy (gamma level + put/call add-on)</h4>
        <div class="bk-f"><span class="bk-blk k-entry">Enter when SPY gamma is at or below <input type="number" step="any" value="${esc(c.entry_level_bn?.SPY ?? -10)}" data-bind="config.entry_level_bn.SPY" data-num> bn</span></div>
        <div class="bk-f"><span class="bk-blk k-add">Add when SPY's put/call is <input type="number" step="0.1" value="${esc(c.add_min_z ?? 1)}" data-bind="config.add_min_z" data-num> std above normal, at most <input type="number" step="1" min="0" value="${esc(c.max_adds ?? 1)}" data-bind="config.max_adds" data-num> time(s)</span></div>
        <div class="bk-f"><span class="bk-blk k-exit">Exit when SPY gamma is back at 0</span></div>
        <div class="bk-f"><span class="bk-blk k-misc">Live record from <input type="date" value="${esc((c.my_strategy_start || "").slice(0, 10))}" data-bind="config.my_strategy_start" data-date></span></div>
        <div class="bk-say">Changing these re-draws the live record. The "My strategy" backtest is a separate study and is not re-run automatically.</div></div>`;
    }
    function tickHTML() {
      const c = S.config, w = S.watchlist || { tickers: [], futures: [] };
      const tags = (arr, path) => arr.map((t, j) => `<span class="bk-tag">${esc(t)}<button data-act="rm" data-path="${path}.${j}" aria-label="Remove ${esc(t)}">×</button></span>`).join("");
      const oi = (c.oi_watch || []).map((o, j) => `<div class="bk-card" style="background:var(--bk-soft)"><div class="bk-f">
          <input value="${esc(o.option)}" data-bind="config.oi_watch.${j}.option" placeholder="Option code, e.g. SPXW261231P07250000" style="flex:1 1 240px" aria-label="Option code">
          <input value="${esc(o.label)}" data-bind="config.oi_watch.${j}.label" placeholder="Name" style="flex:1 1 160px" aria-label="Name">
          alert if open interest drops <input type="number" step="1" value="${esc(o.alert_drop_pct ?? 15)}" data-bind="config.oi_watch.${j}.alert_drop_pct" data-num style="width:80px">%
          <button class="bk-btn dan" data-act="rm" data-path="config.oi_watch.${j}">Remove</button></div>
          <div class="bk-f"><input value="${esc(o.why)}" data-bind="config.oi_watch.${j}.why" placeholder="Why it matters" style="flex:1" aria-label="Why"></div></div>`).join("");
      return `<div class="bk-card"><h4>Tickers the tracker follows</h4>
          <div class="bk-f">${tags(w.tickers || [], "watchlist.tickers")}<input id="bk-newt" placeholder="Add ticker, e.g. TSLA" style="width:170px"><button class="bk-btn" data-act="addt" data-to="tickers">Add</button></div>
          <h4 style="margin-top:10px">Futures</h4><div class="bk-f">${tags(w.futures || [], "watchlist.futures")}<select id="bk-newf"><option>NQ</option><option>ES</option></select><button class="bk-btn" data-act="addt" data-to="futures">Add</button></div></div>
        <div class="bk-card"><h4>Signals and walls</h4>
          <div class="bk-f">Strategy signals come from <select data-bind="config.signal_ticker">${(w.tickers || ["SPY"]).concat(["SPY"]).filter((v, i, a) => a.indexOf(v) === i).map(t => `<option ${t === (c.signal_ticker || "SPY") ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
          <div class="bk-f">Look for the put wall up to <input type="number" step="0.5" value="${esc(c.wall_band_pct?.put ?? 5)}" data-bind="config.wall_band_pct.put" data-num style="width:80px">% below yesterday's close, and the call wall up to <input type="number" step="0.5" value="${esc(c.wall_band_pct?.call ?? 5)}" data-bind="config.wall_band_pct.call" data-num style="width:80px">% above.</div>
          <div class="bk-say">The backtests used 5% / 5%. A change applies from the next trading day.</div></div>
        <div class="bk-card"><h4>Open-interest alerts</h4>${oi}<button class="bk-btn" data-act="addoi">+ Add a contract to watch</button></div>`;
    }
    function layoutHTML() {
      const L = (S.config.layout ??= {}), panels = opts.panels || L.panels || [];
      const order = L.order || panels.map(p => p[3]);
      const sorted = panels.slice().sort((a, b) => (order.indexOf(a[3]) + 1 || 999) - (order.indexOf(b[3]) + 1 || 999));
      const hid = new Set(L.hidden || []), col = new Set(L.collapsed || []), lo = new Set(L.lines_off || []);
      const lines = opts.lines || L.lines || [];
      return `<div class="bk-card"><h4>Page layout</h4><div class="bk-say" style="margin-top:0">Order, what shows and what starts folded. Saved for every browser; ✕ / ⚙ on the page still change just that browser until the next save here.</div>
        <ul class="bk-lay">${sorted.map((p, j) => `<li><button class="bk-x" style="background:var(--bk-soft);color:var(--bk-ink)" data-act="up" data-el="${esc(p[3])}" title="Move up" ${j ? "" : "disabled"}>↑</button>
          <button class="bk-x" style="background:var(--bk-soft);color:var(--bk-ink)" data-act="down" data-el="${esc(p[3])}" title="Move down" ${j < sorted.length - 1 ? "" : "disabled"}>↓</button>
          <b>${esc(p[1])}</b><label class="bk-sw"><input type="checkbox" data-lay="hidden" data-id="${esc(p[0])}" ${hid.has(p[0]) ? "" : "checked"}> show</label>
          <label class="bk-sw"><input type="checkbox" data-lay="collapsed" data-id="${esc(p[0])}" ${col.has(p[0]) ? "checked" : ""}> folded</label>
          ${p[2] && p[2].length ? `<div class="bk-kids">${p[2].map(([k, n]) => `<label class="bk-sw" style="font-weight:600"><input type="checkbox" data-lay="hidden" data-id="${esc(k)}" ${hid.has(k) ? "" : "checked"}> ${esc(n)}</label>`).join("")}</div>` : ""}</li>`).join("")}</ul>
        ${lines.length ? `<h4 style="margin-top:12px">Chart lines</h4><div class="bk-f">${lines.map(([k, n]) => `<label class="bk-sw"><input type="checkbox" data-lay="lines_off" data-id="${esc(k)}" ${lo.has(k) ? "" : "checked"}> ${esc(n)}</label>`).join("")}</div>` : ""}</div>`;
    }
    function render() {
      if (!S) { root.innerHTML = `<div class="bk"><div class="bk-msg">${esc(note || "Loading the settings…")}</div></div>`; return; }
      const tabs = [["strategies", "Strategies"], ["tickers", "Tickers & alerts"], ["layout", "Layout"]];
      const body = tab === "strategies" ? `<div class="bk-grid">${palette()}<div>${strats().map(stratHTML).join("") || '<div class="bk-say">No strategies yet: pick one under NEW STRATEGY.</div>'}</div></div>`
        : tab === "mine" ? mineHTML() : tab === "tickers" ? tickHTML() : layoutHTML();
      const y = window.scrollY, f = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.bind : null;
      root.innerHTML = `<div class="bk"><div class="bk-top"><div class="bk-tabs" role="tablist">${tabs.map(([k, n]) => `<button class="bk-tab" role="tab" aria-selected="${k === tab}" data-tab="${k}">${n}</button>`).join("")}</div>
        <button class="bk-btn" data-act="undo" ${dirty() ? "" : "disabled"}>Undo changes</button><button class="bk-btn pri" data-act="save" ${dirty() && !busy ? "" : "disabled"}>${busy ? "Saving…" : "Save"}</button>
        <div class="bk-msg">${dirty() ? '<span class="bk-dirty">Unsaved changes.</span> ' : ""}${esc(note)}</div></div>${body}</div>`;
      if (f) { const el = root.querySelector(`[data-bind="${window.CSS && CSS.escape ? CSS.escape(f) : f}"]`); if (el) el.focus(); }
      window.scrollTo(0, y);
    }
    function addTo(sid, list, type) {
      const s = strats()[sid]; if (!s || !type) return;
      if (list === "entry") { const d = ENTRY[type]; s.entry = { type, ...(d.p ? { [d.p]: d.d } : {}) }; if (d.side && !s._sideSet) s.side = d.side; return; }
      const defs = list === "exits" ? EXIT : FILTER, d = defs[type];
      (s[list] ??= []).push({ type, ...(d.p ? { [d.p]: d.d } : {}) });
    }
    function validate() {
      const ids = new Set();
      for (const s of strats()) {
        if (!s.id || ids.has(s.id)) return `Two strategies share the id "${s.id}".`; ids.add(s.id);
        if (!(s.exits || []).length) return `"${s.name}" has no exit block.`;
        for (const x of (s.exits || []).concat(s.filters || [], [s.entry || {}])) for (const k of ["bn", "pct", "n"]) if (k in x && !isFinite(+x[k])) return `"${s.name}": a number is missing.`;
      }
      return null;
    }
    root.addEventListener("click", async ev => {
      const t = ev.target.closest("[data-tab],[data-act]"); if (!t || t.tagName === "SELECT") return;
      if (t.dataset.tab) { tab = t.dataset.tab; render(); return; }
      const a = t.dataset.act, sid = +t.dataset.sid;
      if (a === "rm") { const ks = t.dataset.path.split("."), j = +ks.pop(); get(ks.join(".")).splice(j, 1); }
      else if (a === "dup") { const c = clone(strats()[sid]); c.id = "s" + Date.now().toString(36); c.name += " (copy)"; delete c.signal_file; delete c.backtest_file; c.panel = true; strats().splice(sid + 1, 0, c); }
      else if (a === "del") { if (t.dataset.confirm !== "1") { t.dataset.confirm = "1"; t.textContent = "Click again to delete"; return; } strats().splice(sid, 1); }
      else if (a === "new") { const p = clone(PRESETS[t.dataset.preset]); strats().push({ id: "s" + Date.now().toString(36), name: t.dataset.preset === "Empty" ? "New strategy" : t.dataset.preset + " (mine)", on: true, panel: true, trade: "NQ", add_on: { on: false, z: 1 }, start: new Date().toISOString().slice(0, 10) + " 00:00", ...p }); }
      else if (a === "addt") { const w = (S.watchlist ??= { tickers: [], futures: [] }), to = t.dataset.to;
        const v = (to === "tickers" ? root.querySelector("#bk-newt").value : root.querySelector("#bk-newf").value).trim().toUpperCase();
        if (!/^[A-Z0-9.^_=-]{1,12}$/.test(v)) { note = "That doesn't look like a ticker."; render(); return; }
        if (!(w[to] ??= []).includes(v)) w[to].push(v); }
      else if (a === "addoi") (S.config.oi_watch ??= []).push({ option: "", label: "", why: "", alert_drop_pct: 15 });
      else if (a === "up" || a === "down") { const L = S.config.layout, panels = opts.panels || L.panels || [];
        const order = (L.order && L.order.length ? L.order : panels.map(p => p[3])).slice(); panels.forEach(p => { if (!order.includes(p[3])) order.push(p[3]); });
        const i = order.indexOf(t.dataset.el), j = a === "up" ? i - 1 : i + 1; if (i < 0 || j < 0 || j >= order.length) return; [order[i], order[j]] = [order[j], order[i]]; L.order = order; }
      else if (a === "undo") { S = JSON.parse(saved); note = ""; }
      else if (a === "save") {
        const bad = validate(); if (bad) { note = bad; render(); return; }
        busy = true; note = ""; render();
        try { if (S.config.layout) { S.config.layout.version = Date.now(); if (opts.panels) S.config.layout.panels = opts.panels; if (opts.lines) S.config.layout.lines = opts.lines; }
          note = (await opts.save(clone(S))) || "Saved."; saved = JSON.stringify(S); }
        catch (e) { note = "Not saved: " + (e.message || e); }
        busy = false;
      }
      render();
    });
    root.addEventListener("change", ev => {
      const t = ev.target;
      if (t.dataset.act === "addsel") { addTo(+t.dataset.sid, t.dataset.list, t.value); render(); return; }
      if (t.dataset.lay) { const L = (S.config.layout ??= {}), k = t.dataset.lay, st = new Set(L[k] || []);
        const on = k === "collapsed" ? t.checked : !t.checked; on ? st.add(t.dataset.id) : st.delete(t.dataset.id); L[k] = [...st]; render(); return; }
      if (!t.dataset.bind) return;
      let v = t.type === "checkbox" ? t.checked : t.value;
      if (t.dataset.num != null) v = v === "" ? null : +v;
      if (t.dataset.date != null) v = v ? v + " 00:00" : null;
      set(t.dataset.bind, v);
      if (t.dataset.retype != null) { const d = ENTRY[v]; const e = get(t.dataset.bind.replace(/\.type$/, "")); delete e.bn; if (d.p) e[d.p] = d.d; }
      if (/\.side$/.test(t.dataset.bind)) { const s = get(t.dataset.bind.replace(/\.side$/, "")); s._sideSet = true; }
      render();
    });
    root.addEventListener("input", ev => { const t = ev.target; if (t.dataset.bind && t.type !== "checkbox" && t.tagName === "INPUT" && t.dataset.num == null && t.dataset.date == null) set(t.dataset.bind, t.value); });
    // drag and drop from the palette into a slot
    root.addEventListener("dragstart", ev => { const c = ev.target.closest("[data-dk]"); if (c) ev.dataTransfer.setData("text/plain", c.dataset.dk + ":" + c.dataset.dt); });
    root.addEventListener("dragover", ev => { const s = ev.target.closest("[data-drop]"); if (s) { ev.preventDefault(); s.classList.add("over"); } });
    root.addEventListener("dragleave", ev => { const s = ev.target.closest("[data-drop]"); if (s) s.classList.remove("over"); });
    root.addEventListener("drop", ev => {
      const s = ev.target.closest("[data-drop]"); if (!s) return; ev.preventDefault(); s.classList.remove("over");
      const [dk, dt] = (ev.dataTransfer.getData("text/plain") || "").split(":");
      if (dk !== s.dataset.drop) { note = dk === "entry" ? "Entry blocks go in WHEN." : dk === "exits" ? "Exit blocks go in EXIT." : "Filter blocks go in ONLY IF."; render(); return; }
      addTo(+s.dataset.sid, dk, dt); note = ""; render();
    });
    const strip = o => { for (const s of (o.config.strategies || [])) delete s._sideSet; return o; };
    const origSave = opts.save; opts.save = async st => origSave(strip(st));
    (async () => { try { const r = await opts.load(); S = { config: r.config || {}, watchlist: r.watchlist || { tickers: [], futures: [] } }; saved = JSON.stringify(S); note = r.note || ""; }
      catch (e) { note = "Couldn't load the settings: " + (e.message || e); } render(); })();
    render();
    return { reload: async () => { const r = await opts.load(); S = { config: r.config, watchlist: r.watchlist }; saved = JSON.stringify(S); render(); }, isDirty: dirty };
  };
  window.BlocksEditor.describe = describe;
})();
