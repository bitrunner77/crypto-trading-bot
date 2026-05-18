// Polymarket Trading Bot — Singularity Engine
// Pure vanilla JS simulation of a live trading dashboard.
(() => {
  "use strict";

  // ---------- CONFIG ----------
  const MARKETS = [
    { sym: "BTC_5M",  px: 64210, vol: 0.0035, mu:  0.0002 },
    { sym: "ETH_5M",  px:  3120, vol: 0.0040, mu:  0.0003 },
    { sym: "SOL_250", px:  0.62, vol: 0.0090, mu:  0.0005 },
    { sym: "ETH_1H",  px:  3122, vol: 0.0030, mu:  0.0001 },
    { sym: "SOL_1H",  px:  0.61, vol: 0.0080, mu:  0.0002 },
    { sym: "BTC_1H",  px: 64200, vol: 0.0025, mu:  0.0001 },
    { sym: "SOL_4H",  px:  0.59, vol: 0.0070, mu: -0.0002 },
  ];

  const NEWS = [
    "BAYES > OPINIONS",
    "NASH FLIP: Q4 2024",
    "MAKERS +1.12%/TRADE",
    "LIMIT ORDERS > IMPULSE",
    "BAYES UPDATE: PRIOR 0.61",
    "TAKERS PAY IMPULSE TAX",
    "KELLY F* = 0.21",
    "EVENT HORIZON STABLE",
    "REGIME: EQUILIBRIUM DRIFT",
    "SPREAD COMPRESSION −12C",
    "FILL VELOCITY +18%",
    "INFORMED FLOW: NEUTRAL",
    "MICROSTRUCTURE: TIGHT",
    "20 TRADES // BOTS TAX PROFIT BEFORE YOU READ",
  ];

  const rand = (a, b) => a + Math.random() * (b - a);
  const randn = () => {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const money = (n) => {
    const sign = n >= 0 ? "+" : "-";
    return sign + "$" + Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
  };
  const fmtPx = (sym, px) => {
    if (sym.startsWith("SOL")) return px.toFixed(2);
    if (sym.startsWith("ETH")) return px.toFixed(2);
    return px.toFixed(2);
  };

  // ---------- STATE ----------
  const state = {
    pl: 0,
    plPath: [],
    fillsPerMin: 0,
    fillsWindow: [],
    wins: 0,
    losses: 0,
    spreadC: 2.5,
    bookMid: 0.4475,
    bookSpread: 0.123,
    btcPx: 64210,
    btcPath: [],
    nashPath: [],
    kellySurface: [],
    singularityAngle: 0,
    singularityVal: 0.382,
  };

  // ---------- TICKERS ----------
  function buildTicker(el, items) {
    const html = items.map(t => `<span class="item">${t}</span>`).join("");
    el.innerHTML = html + html; // duplicate for seamless scroll
  }
  buildTicker(document.getElementById("topTicker"),
    NEWS.map(t => `<span class="sym">//</span>${t}`));

  function refreshBottomTicker() {
    const items = [];
    for (let i = 0; i < 14; i++) {
      const m = pick(MARKETS);
      const side = Math.random() > 0.5 ? "BUY" : "SELL";
      const cls = side === "BUY" ? "pos" : "neg";
      const sz = rand(0.05, 4).toFixed(2);
      const px = "$" + (m.px * rand(0.999, 1.001)).toFixed(2);
      items.push(`<span class="item"><span class="${cls}">${side}</span> <span class="sym">${m.sym}</span> ${sz} ×${Math.floor(rand(1,12))} <span class="pos">${px}</span></span>`);
    }
    buildTicker(document.getElementById("bottomTicker"), items);
  }
  refreshBottomTicker();
  setInterval(refreshBottomTicker, 12000);

  // ---------- UTC CLOCK ----------
  function tickClock() {
    const d = new Date();
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mm = String(d.getUTCMinutes()).padStart(2, "0");
    const ss = String(d.getUTCSeconds()).padStart(2, "0");
    const lat = Math.floor(rand(8, 42));
    document.getElementById("utcClock").textContent = `UTC ${hh}:${mm}:${ss} LAT ${lat}MS`;
  }
  setInterval(tickClock, 1000); tickClock();

  // ---------- TRADES & FILLS ----------
  function makeTrade() {
    const m = pick(MARKETS);
    const side = Math.random() > 0.48 ? "BUY" : "SELL";
    const sz = +rand(0.05, 0.99).toFixed(2);
    const x = Math.floor(rand(3, 48));
    const pnl = randn() * 1.4 + (Math.random() > 0.45 ? 0.6 : -0.3);
    // Step the market price (random walk)
    m.px = Math.max(0.0001, m.px * (1 + m.mu + randn() * m.vol));
    if (m.sym.startsWith("BTC")) state.btcPx = m.px;
    return { sym: m.sym, side, sz, x, pnl: +pnl.toFixed(2), px: m.px };
  }

  const tradesList = document.getElementById("tradesList");
  const plFills = document.getElementById("plFills");
  const MAX_TRADES_LEFT = 28;
  const MAX_TRADES_PL = 10;

  function pushTrade() {
    const t = makeTrade();
    const cls = t.side === "BUY" ? "buy" : "sell";
    const pnlCls = t.pnl >= 0 ? "pos" : "neg";
    const pnlStr = (t.pnl >= 0 ? "+$" : "-$") + Math.abs(t.pnl).toFixed(2);

    // Left trades panel
    const li = document.createElement("li");
    li.className = cls + " flash";
    li.innerHTML =
      `<span class="side">${t.side === "BUY" ? "BUY " : "SELL"}</span>` +
      `<span class="market">${t.sym}</span>` +
      `<span class="price">${fmtPx(t.sym, t.px)}</span>` +
      `<span class="qty">×${t.x}</span>` +
      `<span class="pnl ${pnlCls}">${pnlStr}</span>`;
    tradesList.insertBefore(li, tradesList.firstChild);
    while (tradesList.children.length > MAX_TRADES_LEFT) tradesList.removeChild(tradesList.lastChild);
    setTimeout(() => li.classList.remove("flash"), 300);

    // Portfolio P/L fills (top-right list inside center panel)
    const li2 = document.createElement("li");
    li2.className = cls;
    li2.innerHTML =
      `<span class="side">${t.side === "BUY" ? "BUY " : "SELL"}</span>` +
      `<span class="market">${t.sym}</span>` +
      `<span class="price">${fmtPx(t.sym, t.px)}</span>` +
      `<span class="qty">×${t.x}</span>` +
      `<span class="pnl ${pnlCls}">${pnlStr}</span>`;
    plFills.insertBefore(li2, plFills.firstChild);
    while (plFills.children.length > MAX_TRADES_PL) plFills.removeChild(plFills.lastChild);

    // Update aggregate P/L
    const dollarPnl = t.pnl * t.x * 30 + randn() * 18;
    state.pl += dollarPnl;
    if (dollarPnl >= 0) state.wins++; else state.losses++;
    state.fillsWindow.push(Date.now());

    return t;
  }

  // ---------- KPI ROW ----------
  function refreshKPI() {
    const now = Date.now();
    state.fillsWindow = state.fillsWindow.filter(t => now - t < 60000);
    const fpm = state.fillsWindow.length;
    state.fillsPerMin = fpm;
    document.getElementById("kpiPL").textContent = money(state.pl);
    document.getElementById("kpiPL").className = "kpi-value " + (state.pl >= 0 ? "green" : "red");
    const total = state.wins + state.losses;
    const wr = total ? (100 * state.wins / total) : 0;
    document.getElementById("kpiWin").textContent = wr.toFixed(1) + "%";
    state.spreadC = clamp(state.spreadC + randn() * 0.05, 1.2, 4.6);
    document.getElementById("kpiSpread").textContent = state.spreadC.toFixed(1) + "C";
    document.getElementById("kpiFills").textContent = String(fpm);
    document.getElementById("plBig").textContent = money(state.pl);
    document.getElementById("plBig").style.color = state.pl >= 0 ? "var(--green)" : "var(--red)";
  }

  // ---------- P/L CURVE ----------
  const plCanvas = document.getElementById("plCurve");
  function drawPLCurve() {
    const c = plCanvas;
    const w = c.width = c.clientWidth;
    const h = c.height = c.clientHeight;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    // grid
    ctx.strokeStyle = "rgba(8,33,55,0.6)";
    ctx.lineWidth = 1;
    for (let i = 0; i < 6; i++) {
      const y = (h / 6) * i;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    for (let i = 0; i < 10; i++) {
      const x = (w / 10) * i;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }

    state.plPath.push(state.pl);
    while (state.plPath.length > 240) state.plPath.shift();
    if (state.plPath.length < 2) return;

    const min = Math.min(...state.plPath, 0);
    const max = Math.max(...state.plPath, 100);
    const span = Math.max(1, max - min);

    // glow line
    const grad = ctx.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, "rgba(0,229,255,0.1)");
    grad.addColorStop(1, "rgba(0,229,255,1)");
    ctx.strokeStyle = grad;
    ctx.lineWidth = 2;
    ctx.shadowColor = "rgba(0,229,255,0.8)";
    ctx.shadowBlur = 8;
    ctx.beginPath();
    state.plPath.forEach((v, i) => {
      const x = (i / (state.plPath.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 8) - 4;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;

    // glow fill
    const fillGrad = ctx.createLinearGradient(0, 0, 0, h);
    fillGrad.addColorStop(0, "rgba(0,229,255,0.18)");
    fillGrad.addColorStop(1, "rgba(0,229,255,0)");
    ctx.fillStyle = fillGrad;
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fill();
  }

  // ---------- SINGULARITY ----------
  const singCanvas = document.getElementById("singularity");
  function drawSingularity(dt) {
    const c = singCanvas;
    const w = c.width = c.clientWidth;
    const h = c.height = c.clientHeight;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2;

    state.singularityAngle += dt * 0.5;
    state.singularityVal = 0.5 + 0.5 * Math.sin(performance.now() / 4200);

    // outer glow
    const glow = ctx.createRadialGradient(cx, cy, 4, cx, cy, Math.min(w, h) * 0.55);
    glow.addColorStop(0, "rgba(0,229,255,0.55)");
    glow.addColorStop(0.4, "rgba(0,160,200,0.35)");
    glow.addColorStop(1, "rgba(0,40,80,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    // particle ring (stippled)
    const N = 280;
    const baseR = Math.min(w, h) * 0.32;
    for (let i = 0; i < N; i++) {
      const t = i / N;
      const a = t * Math.PI * 2 + state.singularityAngle;
      const wobble = Math.sin(a * 5 + state.singularityAngle * 2) * 8;
      const r = baseR + wobble + randn() * 1.2;
      const px = cx + Math.cos(a) * r * 1.4;
      const py = cy + Math.sin(a) * r * 0.55;
      const alpha = 0.4 + 0.6 * Math.abs(Math.sin(a + state.singularityAngle * 1.5));
      ctx.fillStyle = `rgba(${130 + Math.floor(60 * alpha)},${200 + Math.floor(40 * alpha)},255,${alpha * 0.9})`;
      ctx.fillRect(px, py, 1.4, 1.4);
    }

    // inner core
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, 18);
    core.addColorStop(0, "rgba(255,255,255,0.9)");
    core.addColorStop(0.5, "rgba(0,229,255,0.6)");
    core.addColorStop(1, "rgba(0,80,140,0)");
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, 22, 0, Math.PI * 2);
    ctx.fill();

    document.getElementById("singVal").textContent = state.singularityVal.toFixed(3);
  }

  // ---------- PRESSURE BARS ----------
  function buildPressure() {
    const list = document.getElementById("pressureList");
    list.innerHTML = "";
    MARKETS.forEach(m => {
      const row = document.createElement("div");
      row.className = "pressure-row";
      row.innerHTML = `<span class="pname">${m.sym}</span>
        <div class="bar">
          <div class="buy" style="width:50%"></div>
          <div class="sell" style="width:50%"></div>
        </div>`;
      list.appendChild(row);
    });
  }
  buildPressure();

  function updatePressure() {
    document.querySelectorAll(".pressure-row").forEach((row) => {
      const b = clamp(40 + randn() * 18 + Math.sin(performance.now() / 1800) * 6, 8, 92);
      row.querySelector(".buy").style.width = b + "%";
      row.querySelector(".sell").style.width = (100 - b) + "%";
    });
  }

  // ---------- ORDER BOOK ----------
  function buildBook() {
    const asks = document.getElementById("bookAsks");
    const bids = document.getElementById("bookBids");
    asks.innerHTML = "";
    bids.innerHTML = "";
    for (let i = 4; i >= 0; i--) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="px">$0.00</span><span class="depth"><span class="fill"></span></span><span class="sz">0</span>`;
      asks.appendChild(li);
    }
    for (let i = 0; i < 5; i++) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="px">$0.00</span><span class="depth"><span class="fill"></span></span><span class="sz">0</span>`;
      bids.appendChild(li);
    }
  }
  buildBook();

  function updateBook() {
    state.bookMid += randn() * 0.001;
    state.bookSpread = clamp(state.bookSpread + randn() * 0.004, 0.05, 0.25);
    const mid = state.bookMid;
    const half = state.bookSpread / 2;
    const asks = document.getElementById("bookAsks").children;
    const bids = document.getElementById("bookBids").children;
    // asks: highest at top => index 0 highest
    for (let i = 0; i < asks.length; i++) {
      const dist = (asks.length - i) * 0.012 + half;
      const px = mid + dist;
      const sz = Math.floor(rand(60, 290));
      asks[i].querySelector(".px").textContent = "$" + px.toFixed(2);
      asks[i].querySelector(".sz").textContent = sz;
      asks[i].querySelector(".fill").style.width = clamp(sz / 3, 8, 100) + "%";
    }
    for (let i = 0; i < bids.length; i++) {
      const dist = (i + 1) * 0.012 + half;
      const px = mid - dist;
      const sz = Math.floor(rand(80, 410));
      bids[i].querySelector(".px").textContent = "$" + px.toFixed(2);
      bids[i].querySelector(".sz").textContent = sz;
      bids[i].querySelector(".fill").style.width = clamp(sz / 4, 8, 100) + "%";
    }
    const spreadStr = "SPREAD $" + state.bookSpread.toFixed(3);
    document.getElementById("bookSpreadBar").textContent = "SPREAD: $" + state.bookSpread.toFixed(3);
    document.getElementById("bookSpread").textContent = spreadStr;
  }

  // ---------- KELLY SURFACE ----------
  const kellyCanvas = document.getElementById("kellySurface");
  function drawKelly() {
    const c = kellyCanvas;
    const w = c.width = c.clientWidth;
    const h = c.height = c.clientHeight;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    // grid
    ctx.strokeStyle = "rgba(8,33,55,0.5)";
    for (let i = 0; i < 5; i++) {
      const y = (h / 5) * i;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }

    // jagged kelly surface (sin + noise)
    if (state.kellySurface.length === 0) {
      for (let i = 0; i < 90; i++) state.kellySurface.push(rand(0.3, 0.7));
    }
    // shift
    state.kellySurface.shift();
    state.kellySurface.push(clamp(state.kellySurface[state.kellySurface.length - 1] + randn() * 0.08, 0.1, 0.95));

    const data = state.kellySurface;
    ctx.strokeStyle = "#00e5ff";
    ctx.lineWidth = 1.5;
    ctx.shadowColor = "rgba(0,229,255,0.7)";
    ctx.shadowBlur = 6;
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - v * (h - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  // ---------- NASH REGIME ----------
  function drawNash() {
    const c = document.getElementById("nashRegime");
    const w = c.width = c.clientWidth;
    const h = c.height = c.clientHeight;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    ctx.strokeStyle = "rgba(8,33,55,0.5)";
    for (let i = 0; i < 5; i++) {
      const y = (h / 5) * i;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }

    if (state.nashPath.length === 0) {
      let v = 0.5;
      for (let i = 0; i < 90; i++) { v = clamp(v + randn() * 0.05, 0.1, 0.9); state.nashPath.push(v); }
    }
    state.nashPath.shift();
    const last = state.nashPath[state.nashPath.length - 1];
    state.nashPath.push(clamp(last + randn() * 0.05 - (last - 0.5) * 0.02, 0.05, 0.95));

    ctx.strokeStyle = "#ffb347";
    ctx.lineWidth = 1.5;
    ctx.shadowColor = "rgba(255,179,71,0.6)";
    ctx.shadowBlur = 6;
    ctx.beginPath();
    state.nashPath.forEach((v, i) => {
      const x = (i / (state.nashPath.length - 1)) * w;
      const y = h - v * (h - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;

    // equilibrium midline
    ctx.strokeStyle = "rgba(255,179,71,0.2)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
    ctx.setLineDash([]);

    const regimes = ["EQUILIBRIUM DRIFT", "PURE STRATEGY", "MIXED SHIFT", "DOMINANT FLIP"];
    document.getElementById("nashState").textContent =
      regimes[Math.floor(performance.now() / 7000) % regimes.length];
  }

  // ---------- RUNTIME CONSOLE ----------
  const consoleEl = document.getElementById("console");
  const MAX_CONSOLE = 22;
  function consoleLine() {
    const kinds = [
      () => `[EXEC]   <span class="body">FILL NO $<span class="num">${rand(0.20, 0.95).toFixed(2)}</span> x<span class="num">${Math.floor(rand(800, 4900))}</span> EDGE</span>`,
      () => `[ORDER]  <span class="body">SWEEP <span class="num">${Math.floor(rand(120, 990))}</span>@$<span class="num">${rand(0.20, 0.99).toFixed(2)}C</span> <span class="num">${Math.floor(rand(8, 62))}</span>ms</span>`,
      () => `[RISK]   <span class="body">VAR=<span class="num">${Math.floor(rand(800, 1500))}</span> UTIL=<span class="num">${rand(0.10, 0.95).toFixed(2)}</span></span>`,
      () => `[BAYES]  <span class="body">P=<span class="num">${rand(0.15, 0.85).toFixed(3)}</span>-<span class="num">${rand(0.15, 0.85).toFixed(3)}</span> LR=<span class="num">${rand(0.6, 3.2).toFixed(2)}</span></span>`,
      () => `[P/L]    <span class="body">REAL=<span class="pos">+$${Math.floor(rand(20, 580))}</span> FEES=<span class="neg">$${rand(0.2, 6.0).toFixed(2)}</span></span>`,
      () => `[NASH]   <span class="body">${pick(["MAKER SHIFT", "TAKER FOLD", "EQUI HOLD", "DRIFT +"])} <span class="num">${rand(0.5, 3.2).toFixed(2)}</span>PP</span>`,
      () => `[SPREAD] <span class="body">BID=<span class="num">${rand(34, 42).toFixed(1)}</span>C ASK=<span class="num">${rand(60, 78).toFixed(1)}</span>C</span>`,
    ];
    const tagClasses = ["tag-exec","tag-order","tag-risk","tag-bayes","tag-pl","tag-nash","tag-spread"];
    const i = Math.floor(Math.random() * kinds.length);
    const text = kinds[i]();
    const li = document.createElement("li");
    const tagMatch = text.match(/^\[([A-Z\/]+)\]/);
    const tag = tagMatch ? tagMatch[1] : "";
    li.innerHTML = `<span class="tag ${tagClasses[i]}">[${tag}]</span>` + text.replace(/^\[[A-Z\/]+\]\s*/, "");
    consoleEl.insertBefore(li, consoleEl.firstChild);
    while (consoleEl.children.length > MAX_CONSOLE) consoleEl.removeChild(consoleEl.lastChild);
  }

  // ---------- SPECTRUM ----------
  const spectrumState = { phase: 0, bars: new Array(64).fill(0).map(() => rand(0.1, 0.9)) };
  function drawSpectrum() {
    const c = document.getElementById("spectrum");
    const w = c.width = c.clientWidth;
    const h = c.height = c.clientHeight;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    spectrumState.phase += 0.08;
    const N = spectrumState.bars.length;
    const bw = w / N;
    for (let i = 0; i < N; i++) {
      const target = 0.25 + 0.7 * Math.abs(Math.sin(spectrumState.phase + i * 0.42 + Math.sin(spectrumState.phase / 3 + i * 0.07)));
      spectrumState.bars[i] += (target - spectrumState.bars[i]) * 0.25;
      const v = spectrumState.bars[i] + randn() * 0.05;
      const bh = clamp(v, 0.02, 1) * (h - 4);
      const grad = ctx.createLinearGradient(0, h, 0, h - bh);
      grad.addColorStop(0, "rgba(0,80,140,0.4)");
      grad.addColorStop(0.6, "rgba(0,170,230,0.85)");
      grad.addColorStop(1, "rgba(180,240,255,1)");
      ctx.fillStyle = grad;
      ctx.fillRect(i * bw + 1, h - bh, bw - 2, bh);
    }
    document.getElementById("specHz").textContent = N + " BANDS";
  }

  // ---------- BTC PRICE ACTION ----------
  function drawBTC() {
    const c = document.getElementById("btcChart");
    const w = c.width = c.clientWidth;
    const h = c.height = c.clientHeight;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    if (state.btcPath.length === 0) {
      let px = state.btcPx;
      for (let i = 0; i < 200; i++) {
        px = px * (1 + randn() * 0.0015);
        state.btcPath.push(px);
      }
    }
    state.btcPath.shift();
    state.btcPath.push(state.btcPath[state.btcPath.length - 1] * (1 + randn() * 0.0015));

    // grid
    ctx.strokeStyle = "rgba(8,33,55,0.5)";
    for (let i = 0; i < 5; i++) {
      const y = (h / 5) * i;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }

    const min = Math.min(...state.btcPath);
    const max = Math.max(...state.btcPath);
    const span = Math.max(1, max - min);

    // candle-ish bars
    const N = state.btcPath.length;
    const step = w / N;
    ctx.lineWidth = 1;
    for (let i = 1; i < N; i++) {
      const x = i * step;
      const p0 = state.btcPath[i - 1];
      const p1 = state.btcPath[i];
      const up = p1 >= p0;
      const y0 = h - ((p0 - min) / span) * (h - 8) - 4;
      const y1 = h - ((p1 - min) / span) * (h - 8) - 4;
      ctx.strokeStyle = up ? "rgba(0,255,157,0.9)" : "rgba(255,59,92,0.9)";
      ctx.beginPath(); ctx.moveTo(x - step, y0); ctx.lineTo(x, y1); ctx.stroke();
    }

    // last marker
    const last = state.btcPath[N - 1];
    const ly = h - ((last - min) / span) * (h - 8) - 4;
    ctx.fillStyle = "#ff3b5c";
    ctx.beginPath(); ctx.arc(w - 3, ly, 3, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.fillText("!", w - 6, ly - 6);

    document.getElementById("btcMark").textContent = "$" + last.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  // ---------- MAIN LOOP ----------
  let last = performance.now();
  function frame(t) {
    const dt = (t - last) / 1000;
    last = t;
    drawSingularity(dt);
    drawPLCurve();
    drawSpectrum();
    drawBTC();
    drawKelly();
    drawNash();
    updatePressure();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // Discrete update intervals
  setInterval(() => { pushTrade(); refreshKPI(); }, 650);
  setInterval(updateBook, 700);
  setInterval(consoleLine, 380);
  setInterval(refreshKPI, 1000);

  // Resize re-render
  window.addEventListener("resize", () => {
    // canvases re-read their clientWidth/clientHeight each frame, so nothing else needed
  });

  // Seed
  for (let i = 0; i < 18; i++) pushTrade();
  for (let i = 0; i < 14; i++) consoleLine();
  refreshKPI();
  updateBook();
})();
