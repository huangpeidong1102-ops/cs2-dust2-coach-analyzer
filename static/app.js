/* CS2 Demo 分析器前端逻辑 —— 沙二教练版（双栏 UI + 颜色代号） */
const state = {
  session: null,
  result: null,
  current: 0,
  showTrails: true,
  showUtils: true,
  showWalls: false,
  showAim: true,
  showBullets: true,
  playing: false,
  playTimer: null,
  playLast: 0,
  speed: 1,
  rangeStart: 0,
  rangeEnd: 0,
  roundMeta: null,
  mapRounds: {},
  activeTab: "util",
  utilHighlight: null,
  rayCache: null,
  labels: {},        // 玩家名 -> {side, num, me}
  roseBins: null,    // 玫瑰图当前统计，供悬停提示
};

const $ = (id) => document.getElementById(id);
const VERDICT_CLASS = { "优秀": "excellent", "良好": "good", "一般": "normal", "失误": "bad" };
const CT_COLOR = "#4d8dff", T_COLOR = "#ff5a5a", ME_COLOR = "#ffd166";
const AIM_COLOR = "#7de0c4", BULLET_COLOR = "#ffe66d";
const CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];
const UTIL_STYLE = {
  smoke: ["#b8c0cc", "烟"], flash: ["#ffe66d", "闪"], he: ["#ff6b6b", "雷"],
  fire: ["#ff7b39", "火"], decoy: ["#b388ff", "诱"],
};
const DIR_NAMES = ["右", "右上", "上", "左上", "左", "左下", "下", "右下"];
const WEAPON_DISPLAY = {
  ak47: "AK-47", m4a1: "M4A4", m4a1_s: "M4A1-S", aug: "AUG", sg553: "SG 553",
  galilar: "Galil", famas: "FAMAS", awp: "AWP", ssg08: "SSG 08",
  mac10: "MAC-10", mp9: "MP9", mp7: "MP7", mp5sd: "MP5-SD", ump45: "UMP-45",
  p90: "P90", bizon: "PP-Bizon", nova: "Nova", xm1014: "XM1014", mag7: "MAG-7",
  sawedoff: "Sawed-Off", deagle: "沙鹰", fiveseven: "Five-SeveN", tec9: "Tec-9",
  p250: "P250", cz75a: "CZ75", elite: "双枪", glock: "Glock", usp_s: "USP-S",
  hkp2000: "P2000", revolver: "R8",
};
const TIER_CLS = { eco: "eco", half: "half", full: "full" };
const dirName = (yaw) => DIR_NAMES[Math.round((((yaw % 360) + 360) % 360) / 45) % 8];

const radarImage = new Image();
radarImage.src = "/static/dust2_radar.png";
radarImage.onload = () => { if (state.result) drawMap(+$("timeSlider").value || 0); };
const wallImage = new Image();
wallImage.src = "/static/dust2_wallmask.png";
let wallCanvas = null, wallCtx = null, wallData = null;
wallImage.onload = () => {
  wallCanvas = document.createElement("canvas");
  wallCanvas.width = wallImage.naturalWidth;
  wallCanvas.height = wallImage.naturalHeight;
  wallCtx = wallCanvas.getContext("2d", { willReadFrequently: true });
  wallCtx.drawImage(wallImage, 0, 0);
  wallData = wallCtx.getImageData(0, 0, wallCanvas.width, wallCanvas.height).data;
  // 只保留墙体轮廓，避免整片红色遮住雷达。
  const W = wallCanvas.width, H = wallCanvas.height;
  const edge = wallCtx.createImageData(W, H);
  const isWall = (x, y) => x >= 0 && y >= 0 && x < W && y < H && wallData[(y * W + x) * 4 + 3] > 20;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (!isWall(x, y)) continue;
      if (isWall(x - 1, y) && isWall(x + 1, y) && isWall(x, y - 1) && isWall(x, y + 1)) continue;
      const i = (y * W + x) * 4;
      edge.data[i] = 255; edge.data[i + 1] = 96; edge.data[i + 2] = 110; edge.data[i + 3] = 210;
    }
  }
  wallCtx.putImageData(edge, 0, 0);
  if (state.result) { drawMap(+$("timeSlider").value || 0); updateAimWindow(); }
};

/* ---------------- 颜色代号 ---------------- */
// 除了开局选人，全场不再出现玩家名：CT=蓝、T=红、你=金圈+(你)。
// 编号按各阵营首次出场顺序固定，跨回合稳定。
function buildLabels() {
  const counters = { CT: 0, T: 0 };
  const labels = {};
  for (const rd of state.result.rounds) {
    for (const r of (rd.roster || [])) {
      if (labels[r.name]) continue;
      counters[r.side] = (counters[r.side] || 0) + 1;
      labels[r.name] = { side: r.side, num: counters[r.side], me: r.name === state.result.player };
    }
  }
  state.labels = labels;
}

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function tagHtml(name) {
  if (!name) return "未知";
  const l = state.labels[name];
  if (!l) return esc(name);
  const txt = (l.side === "CT" ? "蓝CT" : "红T") + (CIRCLED[l.num - 1] || l.num);
  return `<i class="ptag ${l.side === "CT" ? "ct" : "t"}${l.me ? " me" : ""}">${txt}${l.me ? "(你)" : ""}</i>`;
}

// 把文本里的玩家名替换成颜色代号，其余内容做 HTML 转义。
function colorize(text) {
  if (text == null) return "";
  const names = Object.keys(state.labels).sort((a, b) => b.length - a.length);
  if (!names.length) return esc(text);
  const re = new RegExp("(" + names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "g");
  return String(text).split(re).map((part) =>
    state.labels[part] ? tagHtml(part) : esc(part)).join("");
}

/* ---------------- 上传 ---------------- */
const dz = $("dropzone"), fileInput = $("fileInput");
$("pickBtn").addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("drag");
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) handleFile(fileInput.files[0]);
});

function setStatus(msg, cls = "") {
  const el = $("parseStatus");
  el.textContent = msg;
  el.className = "status " + cls;
}

async function handleFile(file) {
  if (!file.name.toLowerCase().endsWith(".dem")) {
    setStatus("请选择 .dem 文件", "error"); return;
  }
  setStatus(`正在上传并解析 ${file.name}，大文件可能需要 1–2 分钟...`);
  const fd = new FormData();
  fd.append("demo", file);
  try {
    const resp = await fetch("/api/upload", { method: "POST", body: fd });
    const j = await resp.json();
    if (!resp.ok) { setStatus(j.error || "解析失败", "error"); return; }
    state.session = j.session;
    state.mapRounds = {};
    setStatus(`解析成功：地图 ${j.map}，共 ${j.round_count} 个回合（已跳过热身）`, "ok");
    renderSetup(j);
  } catch (e) {
    setStatus("网络/服务错误：" + e.message, "error");
  }
}

function renderSetup(j) {
  // 新 demo 解析成功后，重新展开上传/选人区（报告生成后它曾被折叠）。
  $("setupArea").classList.remove("hidden");
  $("setupBar").classList.add("hidden");
  $("setupCard").classList.remove("hidden");
  $("setupCard").scrollIntoView({ behavior: "smooth", block: "center" });
  $("matchInfo").innerHTML = `地图：<b>${j.map}</b> &nbsp; 回合数：<b>${j.round_count}</b>`;
  const sel = $("playerSelect");
  sel.innerHTML = j.players.map((p) =>
    `<option value="${esc(p.name)}">${esc(p.name)}（${p.sides.join("/")}）</option>`).join("");
}

$("analyzeBtn").addEventListener("click", async () => {
  const player = $("playerSelect").value;
  const btn = $("analyzeBtn");
  btn.disabled = true; btn.textContent = "分析中...";
  try {
    const resp = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: state.session, player }),
    });
    const j = await resp.json();
    if (!resp.ok) { alert(j.error || "分析失败"); return; }
    state.result = j;
    state.current = 0;
    state.utilHighlight = null;
    buildLabels();
    renderReport();
  } catch (e) {
    alert("服务错误：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "开始解析";
  }
});

$("changeSetupBtn").addEventListener("click", () => {
  $("setupArea").classList.remove("hidden");
  $("setupBar").classList.add("hidden");
  $("setupArea").scrollIntoView({ behavior: "smooth" });
});

/* ---------------- 报告渲染 ---------------- */
function renderReport() {
  const r = state.result;
  $("report").classList.remove("hidden");
  $("setupArea").classList.add("hidden");
  $("setupBar").classList.remove("hidden");
  $("setupBarInfo").innerHTML = `你 = <b class="hl">${esc(r.player)}</b>（地图金圈、文字里的 <i class="ptag ct me">…(你)</i>）`;
  $("reportPlayer").textContent = `${r.player}（你）`;
  $("youBadge").innerHTML = `本回合你是 <b>${r.rounds[0] ? (r.rounds[0].side === "CT" ? "蓝CT" : "红T") : ""}</b> 金圈`;

  const ov = r.overview;
  const ms = ov.match_score || { T: 0, CT: 0 };

  // 顶部计分横幅：CT vs T 终局比分 + 你方胜负
  $("heroCt").textContent = ms.CT;
  $("heroT").textContent = ms.T;
  const lastSide = r.rounds.length ? r.rounds[r.rounds.length - 1].side : "CT";
  const myScore = ms[lastSide] || 0, oppScore = ms[lastSide === "CT" ? "T" : "CT"] || 0;
  const heroRes = $("heroResult");
  heroRes.textContent = myScore > oppScore ? "你方获胜" : myScore < oppScore ? "你方落败" : "平局";
  heroRes.className = "hs-result " + (myScore > oppScore ? "win" : myScore < oppScore ? "lose" : "draw");

  const items = [
    ["大比分 T:CT", `${ms.T} : ${ms.CT}`],
    ["分析回合", ov.rounds], ["总击杀", ov.kills], ["总死亡", ov.deaths],
    ["K/D", ov.kd], ["总伤害", ov.damage], ["平均分", ov.avg_score],
    ["道具使用", ov.utility_used], ["雷火伤害", ov.utility_damage], ["闪光白敌", ov.flash_enemies],
    ["最佳回合", "第" + ov.best_round + "回合"], ["最差回合", "第" + ov.worst_round + "回合"],
  ];
  const eco = ov.economy;
  if (eco) {
    items.push(["总消费", "$" + eco.total_spend], ["阵亡装备损失", "$" + eco.equip_lost],
      ["eco 局胜", eco.eco_wins + "/" + eco.eco_rounds],
      ["每千元伤害", eco.damage_per_1000]);
  }
  $("overview").innerHTML = items.map(([l, n]) =>
    `<div class="ov-item"><div class="num">${n}</div><div class="lbl">${l}</div></div>`).join("");

  $("sideSummary").innerHTML = Object.entries(ov.side_summary).map(([side, s]) =>
    `<div class="side-box ${side === "T" ? "t" : ""}">
      <b>${side === "T" ? "红T" : "蓝CT"} 方</b>（${s.rounds} 回合）：击杀 ${s.kills} / 死亡 ${s.deaths}，
      胜率 ${s.win_rate}%，平均分 ${s.avg_score}
    </div>`).join("");

  const plan = r.coaching_plan || [];
  $("planWrap").classList.toggle("hidden", !plan.length);
  $("coachPlan").innerHTML = plan.length ? `
    <div class="plan-grid">${plan.map((p, i) => `
      <div class="plan-item">
        <div class="plan-no">${esc(p.priority || "P" + (i + 1))}</div>
        <div class="plan-body">
          <h4>${esc(p.title)}</h4>
          <div class="plan-why">${colorize(p.why)}</div>
          <div class="plan-drill"><b>怎么练：</b>${colorize(p.drill)}</div>
          ${p.rounds && p.rounds.length ? `<div class="plan-refs">回看：${p.rounds.map((idx) =>
            `<button class="plan-ref" data-idx="${idx}">第${idx + 1}回合</button>`).join("")}</div>` : ""}
        </div>
      </div>`).join("")}</div>` : "";
  document.querySelectorAll(".plan-ref").forEach((b) =>
    b.addEventListener("click", () => {
      selectRound(+b.dataset.idx);
      $("roundDetail").scrollIntoView({ behavior: "smooth", block: "start" });
    }));

  $("roundChips").innerHTML = r.rounds.map((rd) => {
    const cls = VERDICT_CLASS[rd.verdict];
    const initial = { "优秀": "优", "良好": "良", "一般": "中", "失误": "失" }[rd.verdict];
    const sideCls = rd.side === "CT" ? "s-ct" : "s-t";
    const sc = rd.score_after || { T: 0, CT: 0 };
    const lbl = rd.round_label ? ` · ${rd.round_label}` : "";
    return `<div class="chip v-${cls} ${sideCls}" data-idx="${rd.round_index}" title="第${rd.round}回合 ${rd.side}方 ${rd.won == null ? "" : (rd.won ? "胜" : "负")}${lbl} · 比分 ${sc.T}:${sc.CT}">
      ${rd.round}<span class="v"><i class="vd vd-${cls}">${initial}</i>${rd.side === "CT" ? "蓝" : "红"}</span><span class="sc">${sc.T}:${sc.CT}</span></div>`;
  }).join("");
  document.querySelectorAll(".chip").forEach((c) =>
    c.addEventListener("click", () => {
      selectRound(+c.dataset.idx);
      $("roundDetail").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }));

  renderDash(r);
  selectRound(0);
}

/* ---------------- 教练仪表盘 ---------------- */
function renderDash(r) {
  const cr = r.coach_report;
  const ov = r.overview;
  if (!cr) return;
  const m = cr.metrics, rk = cr.ranks || {};

  $("styleMain").textContent = cr.style.main;
  const sub = $("styleSub");
  if (cr.style.sub) { sub.textContent = cr.style.sub; sub.classList.remove("hidden"); }
  else sub.classList.add("hidden");
  $("coachSummary").textContent = cr.summary;

  // 评分环
  const avg = ov.avg_score;
  $("dashAvg").textContent = avg;
  const ring = $("scoreRing").getContext("2d");
  ring.clearRect(0, 0, 150, 150);
  ring.lineWidth = 6; ring.lineCap = "butt";
  ring.strokeStyle = "#1f262e";
  ring.beginPath(); ring.arc(75, 75, 68, 0, Math.PI * 2); ring.stroke();
  ring.strokeStyle = "#f0a13a";
  ring.beginPath(); ring.arc(75, 75, 68, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(100, avg) / 100); ring.stroke();

  // 职业指标卡
  const cards = [
    { label: "K / D / A", value: `${m.kills} / ${m.deaths} / ${m.assists}`,
      sub: `K/D ${m.kd} · 击杀全场第 ${rk.kills ? rk.kills.rank : "—"}`, cls: "" },
    { label: "ADR 场均伤害", value: m.adr,
      sub: `全场第 ${rk.adr ? rk.adr.rank : "—"}/${rk.adr ? rk.adr.of : "—"}`, cls: "cyan" },
    { label: "KAST 回合贡献", value: m.kast + "%",
      sub: `${m.kast_rounds}/${m.rounds} 回合有击杀/助攻/存活/被补枪`, cls: m.kast >= 70 ? "gold" : "" },
    { label: "首杀 / 首死", value: `${m.first_kills} / ${m.first_deaths}`,
      sub: m.entry_success == null ? "无首杀首死记录" : `首杀成功率 ${m.entry_success}%`,
      cls: m.first_kills > m.first_deaths ? "gold" : "red" },
    { label: "爆头率", value: m.hs_pct + "%", sub: "击杀质量", cls: "" },
    { label: "存活率", value: Math.round(m.survival_rate * 100) + "%",
      sub: "不轻易送枪才有残局", cls: "" },
  ];
  $("metricRow").innerHTML = cards.map((c) => `
    <div class="metric-card ${c.cls}">
      <div class="mc-label">${c.label}</div>
      <div class="mc-value">${c.value}</div>
      <div class="mc-sub">${c.sub}</div>
    </div>`).join("");

  // 半场/加时状态
  $("halfRow").innerHTML = (cr.halves || []).map((h) => `
    <div class="half-chip ${h.side === "CT" ? "ct" : "t"}">
      <b>${h.label}（${h.side === "CT" ? "蓝CT" : "红T"}）</b>
      <span>场均 ${h.avg_score} 分 · KAST ${h.kast}% · ${h.rounds} 回合</span>
    </div>`).join("");

  // 强项 / 短板
  $("strengthList").innerHTML = (cr.strengths || []).map((s) => `
    <div class="sw-item"><b>${esc(s.title)}</b><p>${esc(s.detail)}</p></div>`).join("");
  $("weakList").innerHTML = (cr.weaknesses || []).map((w) => `
    <div class="sw-item"><b><span class="prio ${w.priority || "P1"}">${w.priority || "P1"}</span>${esc(w.title)}</b><p>${esc(w.detail)}</p></div>`).join("");

  // 关键回合
  const KIND = { eco: ["eco 翻盘", "gold"], force: ["强起翻盘", "gold"], clutch: ["残局", "cyan"],
                 multi: ["多杀", "cyan"], best: ["最佳", "gold"], worst: ["最差", "red"] };
  $("keyRounds").innerHTML = `<h3>关键回合 <span class="mini-note">教练建议优先回看这几回合</span></h3>
    <div class="key-grid">${(cr.key_rounds || []).map((k) => {
      const [label, cls] = KIND[k.kind] || ["关键", "cyan"];
      return `<div class="key-card" data-idx="${k.round_index}">
        <div class="key-head"><span class="key-kind ${cls}">${label}</span><b>第 ${k.round} 回合</b></div>
        <div class="key-title">${esc(k.title)}</div>
        <p>${esc(k.note)}</p>
      </div>`;
    }).join("")}</div>`;
  document.querySelectorAll(".key-card").forEach((el) =>
    el.addEventListener("click", () => {
      selectRound(+el.dataset.idx);
      $("roundDetail").scrollIntoView({ behavior: "smooth", block: "start" });
    }));

  // 对手侦察
  const sc = cr.opponent_scout;
  $("scoutWrap").classList.toggle("hidden", !sc && !(cr.routes || []).length && !cr.momentum.after_loss);
  $("scoutThreats").innerHTML = sc && sc.threats.length ? sc.threats.map((t) => `
    <div class="scout-item"><b>${esc(t.role)}</b><span class="scout-k">${t.kills}</span><p>${esc(t.detail)}</p></div>`).join("")
    : '<div class="scout-empty">对手没有突出的单项威胁。</div>';
  $("scoutAdvice").innerHTML = sc && sc.advice.length
    ? sc.advice.map((a) => `<div class="scout-advice">${esc(a)}</div>`).join("") : "";
  if (sc && sc.site_pref) {
    $("scoutAdvice").innerHTML += `<div class="scout-site">对手下包：A 区 ${sc.site_pref.a_plants} 次 · B 区 ${sc.site_pref.b_plants} 次</div>`;
  }

  // 进攻路线胜率
  const routes = cr.routes || [];
  $("routeBars").innerHTML = routes.length ? routes.map((r) => `
    <div class="route-row">
      <span class="route-name">${esc(r.route)}</span>
      <div class="route-track"><i style="width:${r.win_rate}%"></i></div>
      <span class="route-val">${r.win_rate}% <em>${r.wins}/${r.rounds}</em></span>
    </div>`).join("")
    : '<div class="scout-empty">T 侧样本不足，暂无路线统计。</div>';

  // 心态韧性
  const mo = cr.momentum || {};
  $("momentumBox").innerHTML = mo.after_loss != null ? `
    <div class="momentum ${mo.resilience}">
      <b>连败后场均 ${mo.after_loss}</b><span>全场均值 ${mo.baseline} · ${mo.after_loss_n} 回合</span>
      <p>${esc(mo.note || "")}</p>
    </div>` : '<div class="scout-empty">没有足够连败样本评估心态。</div>';

  drawTrend(r);
}

/* 逐回合状态曲线：金线总分 + 5回合移动均值 + CT/T 底色，点击跳回合 */
function drawTrend(r) {
  const cv = $("trendCanvas"), ctx = cv.getContext("2d");
  const rounds = r.rounds;
  const W = cv.width, H = cv.height;
  const padL = 34, padR = 12, padT = 14, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  ctx.clearRect(0, 0, W, H);
  const x = (i) => padL + (rounds.length <= 1 ? iw / 2 : i / (rounds.length - 1) * iw);
  const y = (v) => padT + (100 - v) / 100 * ih;

  // CT/T 背景分段
  let segStart = 0;
  for (let i = 1; i <= rounds.length; i++) {
    if (i === rounds.length || rounds[i].side !== rounds[segStart].side) {
      const x0 = padL + (segStart / rounds.length) * iw;
      const x1 = padL + (i / rounds.length) * iw;
      ctx.fillStyle = rounds[segStart].side === "CT" ? "rgba(77,141,255,0.07)" : "rgba(255,90,90,0.06)";
      ctx.fillRect(x0, padT, x1 - x0, ih);
      ctx.fillStyle = rounds[segStart].side === "CT" ? "rgba(141,185,255,0.65)" : "rgba(255,138,138,0.6)";
      ctx.font = "13px Bahnschrift, sans-serif";
      ctx.fillText(rounds[segStart].side === "CT" ? "蓝CT" : "红T", x0 + 5, padT + 13);
      segStart = i;
    }
  }
  // 网格与刻度
  ctx.strokeStyle = "rgba(255,255,255,0.06)"; ctx.fillStyle = "#6b7889"; ctx.font = "12px Bahnschrift, sans-serif";
  for (const v of [0, 25, 50, 75, 100]) {
    ctx.beginPath(); ctx.moveTo(padL, y(v)); ctx.lineTo(W - padR, y(v)); ctx.stroke();
    ctx.fillText(v, 6, y(v) + 3);
  }
  // 移动均值（5回合）
  const ma = rounds.map((_, i) => {
    const s = Math.max(0, i - 4);
    const arr = rounds.slice(s, i + 1).map((q) => q.scores.total);
    return arr.reduce((a, b) => a + b, 0) / arr.length;
  });
  ctx.strokeStyle = "rgba(255,255,255,0.45)"; ctx.setLineDash([5, 4]); ctx.lineWidth = 1.4;
  ctx.beginPath();
  ma.forEach((v, i) => { i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)); });
  ctx.stroke(); ctx.setLineDash([]);
  // 总分线
  ctx.strokeStyle = "#f0a13a"; ctx.lineWidth = 2.2; ctx.lineJoin = "round";
  ctx.beginPath();
  rounds.forEach((rd, i) => { i ? ctx.lineTo(x(i), y(rd.scores.total)) : ctx.moveTo(x(i), y(rd.scores.total)); });
  ctx.stroke();
  rounds.forEach((rd, i) => {
    ctx.fillStyle = rd.won ? "#f0a13a" : "#55606e";
    ctx.beginPath(); ctx.arc(x(i), y(rd.scores.total), 4, 0, Math.PI * 2); ctx.fill();
    if (rd.won) { ctx.strokeStyle = "#0d1117"; ctx.lineWidth = 1.2; ctx.stroke(); }
    ctx.fillStyle = "#7d8a9b"; ctx.font = "12px Bahnschrift, sans-serif"; ctx.textAlign = "center";
    ctx.fillText(rd.round, x(i), H - 8); ctx.textAlign = "left";
  });
  // 点击跳转
  cv.onclick = (ev) => {
    const rect = cv.getBoundingClientRect();
    const px = (ev.clientX - rect.left) / rect.width * W;
    let best = 0, bd = 1e9;
    rounds.forEach((_, i) => { const d = Math.abs(x(i) - px); if (d < bd) { bd = d; best = i; } });
    selectRound(rounds[best].round_index);
    $("roundDetail").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  cv.style.cursor = "pointer";
}

$("prevRound").addEventListener("click", () => stepRound(-1));
$("nextRound").addEventListener("click", () => stepRound(1));
function stepRound(d) {
  const rounds = state.result.rounds;
  const i = rounds.findIndex((r) => r.round_index === state.current);
  const j = i + d;
  if (j >= 0 && j < rounds.length) selectRound(rounds[j].round_index);
}

async function selectRound(idx) {
  state.current = idx;
  state.utilHighlight = null;
  document.querySelectorAll(".chip").forEach((c) =>
    c.classList.toggle("active", +c.dataset.idx === idx));
  const rd = state.result.rounds.find((x) => x.round_index === idx);
  if (!rd) return;
  state.roundMeta = state.mapRounds[idx] || null;

  const resultTxt = rd.won == null ? "" : (rd.won ? " · 胜" : " · 负");
  const sc = rd.score_after || { T: 0, CT: 0 };
  $("roundNo").textContent = String(rd.round).padStart(2, "0");
  $("roundTitle").textContent = `第 ${rd.round} 回合${resultTxt}`;
  const lbl = $("roundLabel");
  if (rd.round_label) { lbl.textContent = rd.round_label; lbl.classList.remove("hidden"); }
  else lbl.classList.add("hidden");
  // 迷你记分牌：该回合结束后的比分，你所在阵营带金边
  const mine = (side) => side === rd.side ? " mine" : "";
  $("roundScore").innerHTML =
    `<span class="rs-s ct${mine("CT")}">CT</span><b>${sc.CT}</b><i>:</i><b>${sc.T}</b><span class="rs-s t${mine("T")}">T</span>`;
  $("youBadge").innerHTML = `你是 <i class="ptag ${rd.side === "CT" ? "ct" : "t"} me">${rd.side === "CT" ? "蓝CT" : "红T"}(你)</i>`;
  const v = $("roundVerdict");
  v.textContent = rd.verdict + " · " + rd.scores.total + "分";
  v.className = "verdict " + VERDICT_CLASS[rd.verdict];

  renderCoach(rd);
  renderScores(rd);
  renderDeathReview(rd);
  renderUtility(rd);
  renderEconomy(rd);
  renderComments(rd);
  stopPlay();

  if (!state.roundMeta) {
    $("timeline").innerHTML = '<div class="tl-item">地图数据加载中…</div>';
    $("mapClock").textContent = "加载中…";
  }
  let mr;
  try {
    mr = await loadRound(idx);
  } catch (e) {
    $("timeline").innerHTML = `<div class="tl-item">地图数据加载失败：${esc(e.message)}</div>`;
    return;
  }
  if (state.current !== idx) return;
  state.roundMeta = mr;
  renderTimeline(rd, mr);
  setupSlider(mr);
  setupAimRange(rd, mr);
}

async function loadRound(idx) {
  if (state.mapRounds[idx]) return state.mapRounds[idx];
  const resp = await fetch(`/api/round?session=${encodeURIComponent(state.session)}&index=${idx}`);
  if (!resp.ok) {
    const j = await resp.json().catch(() => ({}));
    throw new Error(j.error || "HTTP " + resp.status);
  }
  const mr = await resp.json();
  state.mapRounds[idx] = mr;
  return mr;
}

function renderCoach(rd) {
  const c = rd.coach || {};
  $("coachPhase").textContent = `${rd.side === "CT" ? "蓝CT" : "红T"} 方 · ${c.phase || "回合"}`;
  $("coachScore").textContent = `教练评分 ${rd.scores.total}`;
  $("coachHeadline").innerHTML = colorize(c.headline || rd.verdict);
  $("coachNote").innerHTML = colorize(c.note || "本回合没有额外教练判词。");
  const storyEl = $("coachStory");
  if (storyEl) {
    if (rd.story) { storyEl.innerHTML = `<b>教练解说</b>${colorize(rd.story)}`; storyEl.classList.remove("hidden"); }
    else storyEl.classList.add("hidden");
  }
  const chips = [];
  if (rd.score_after) {
    chips.push(`<span class="coach-chip score">本回合后比分 <b>${rd.score_after.T} : ${rd.score_after.CT}</b>（T:CT）</span>`);
  }
  if (rd.win_reason) {
    chips.push(`<span class="coach-chip">回合结果 <b>${rd.winner === "CT" ? "蓝CT" : "红T"} 胜</b> · ${esc(rd.win_reason)}</span>`);
  }
  if (c.exchange) {
    chips.push(`<span class="coach-chip">交换价值 <b>${c.exchange.value > 0 ? "+" : ""}${c.exchange.value}</b> · ${esc(c.exchange.label)}</span>`);
  }
  if (c.survival && c.survival.label) {
    chips.push(`<span class="coach-chip">存活判断 <b>${c.survival.value > 0 ? "+" : ""}${c.survival.value}</b> · ${esc(c.survival.label)}</span>`);
  }
  if (c.team_after_death) {
    chips.push(`<span class="coach-chip">死后 6 秒 · 队友 ${c.team_after_death.mate_kills} 杀 / ${c.team_after_death.mate_deaths} 死</span>`);
  }
  if (c.wall) {
    chips.push(`<span class="coach-chip">墙体近似 · 贴脸墙 ${c.wall.facewall_pct}%</span>`);
  }
  $("coachMeta").innerHTML = chips.join("");

  const adv = [];
  (c.position_advice || []).forEach((a) =>
    adv.push(`<div class="advice pos"><b>选位</b>${colorize(a)}</div>`));
  (c.utility_advice || []).forEach((a) =>
    adv.push(`<div class="advice util"><b>道具</b>${colorize(a)}</div>`));
  (c.economy_advice || []).forEach((a) =>
    adv.push(`<div class="advice eco"><b>经济</b>${colorize(a)}</div>`));
  $("adviceRow").innerHTML = adv.join("");
}

function renderScores(rd) {
  const sc = rd.scores;
  const bars = [
    ["选位", sc.positioning], ["配合", sc.teamplay],
    ["决策", sc.decision], ["准星", sc.crosshair], ["总分", sc.total],
  ];
  $("scoreBars").innerHTML = bars.map(([l, n]) =>
    `<div class="score-mini${l === "总分" ? " total" : ""}">
      <span class="sl">${l}</span>
      <div class="bar-track"><div class="bar-fill${l === "总分" ? " total" : ""}" style="width:${n}%"></div></div>
      <span class="sn">${n}</span></div>`).join("");
}

function renderTimeline(rd, mr) {
  $("timeline").innerHTML = rd.timeline.map((t) =>
    `<div class="tl-item ${t.type}" data-tick="${mr.live_start + Math.round(t.t * 64)}">
      <span class="t">${t.t}s</span><span class="e">${colorize(t.event)}</span></div>`).join("");
  $("timeline").querySelectorAll(".tl-item").forEach((el) => {
    el.addEventListener("click", () => jumpTo(Math.min(+el.dataset.tick, mr.max_tick)));
  });
}

function renderDeathReview(rd) {
  const d = rd.death_review;
  if (!d) {
    $("deathReview").innerHTML =
      `<div class="survive-note">本回合存活（存活 ${rd.stats.alive_seconds}s）。<br>${colorize(rd.coach.survival.reason)}</div>`;
    return;
  }
  const wallTxt = d.los_blocked
    ? "雷达线穿过遮挡（可能是高低差/薄墙，仅作近似）"
    : "雷达视线基本畅通";
  const fields = [
    ["死亡时间", d.time + "s"], ["死亡地点", esc(d.place)],
    ["击杀者", tagHtml(d.killer)], ["杀手位置", esc(d.killer_place)],
    ["交火距离", d.killer_distance + "m"], ["武器", esc(d.weapon)],
    ["爆头", d.headshot ? "是" : "否"], ["烟中击杀", d.thrusmoke ? "是" : "否"],
    ["准星偏差", d.yaw_to_killer + "°"],
    ["死亡瞬间准星到墙", d.crosshair_wall_m == null ? "无墙体" : d.crosshair_wall_m + "m"],
    ["雷达墙体判定", wallTxt],
  ];
  let html = `<div class="dr-grid">${fields.map(([k, v]) =>
    `<span>${k}</span><b>${v}</b>`).join("")}</div>`;
  if (d.position_summary) html += `<div class="dr-summary">${colorize(d.position_summary)}</div>`;
  html += `<div class="dr-list">队友（存活）：${
    d.teammates.length ? d.teammates.map((t) =>
      `<div class="li mate"><span>${tagHtml(t.name)} · ${esc(t.landmark)}</span><span>${t.distance}m</span></div>`).join("")
      : '<span class="empty">无存活队友</span>'}</div>`;
  html += `<div class="dr-list">敌人（存活）：${
    d.enemies.length ? d.enemies.map((t) =>
      `<div class="li enemy"><span>${tagHtml(t.name)} · ${esc(t.landmark)}</span><span>${t.distance}m</span></div>`).join("")
      : '<span class="empty">无存活敌人</span>'}</div>`;
  $("deathReview").innerHTML = html;
}

/* ---------------- 道具（与地图联动） ---------------- */
function renderUtility(rd) {
  const labels = { smoke: "烟", flash: "闪", he: "雷", fire: "火", decoy: "诱饵" };
  const summary = ["T", "CT"].map((side) => {
    const u = rd.utility_report[side];
    const counts = Object.entries(labels).map(([k, v]) =>
      `<span>${v} ${u.counts[k] || 0}</span>`).join("");
    return `<div class="util-side-box ${side === "T" ? "t" : "ct"}">
      <div class="usb-head"><b>${side === "T" ? "红T" : "蓝CT"}</b><span>共 ${u.total} 件</span></div>
      <div class="utility-counts">${counts}</div>
      <div class="usb-tactic">${colorize(u.tactic)}</div>
    </div>`;
  }).join("");
  $("utilSummary").innerHTML = summary;

  const events = [];
  for (const side of ["T", "CT"]) {
    for (const e of rd.utility_report[side].events) events.push({ ...e, side });
  }
  events.sort((a, b) => a.tick - b.tick);
  $("utilList").innerHTML = events.length ? events.map((e, i) => {
    const [color, letter] = UTIL_STYLE[e.type] || ["#b388ff", "?"];
    return `<div class="util-item${e.is_own ? " own" : ""}" data-i="${i}">
      <span class="ut">${e.time}s</span>
      <span class="uicon" style="background:${color}">${letter}</span>
      <div class="ubody">
        <div class="um">${tagHtml(e.user)} · ${esc(e.name)}</div>
        <div class="ud">${esc(e.throw_landmark)}${e.estimated_throw ? "（估算）" : ""} → ${esc(e.det_landmark)}</div>
        ${e.effect_text ? `<div class="ue">${colorize(e.effect_text)}</div>` : ""}
      </div>
      <span class="ugo">定位›</span>
    </div>`;
  }).join("") : '<div class="utility-empty">本回合没有道具爆点记录</div>';

  $("utilList").querySelectorAll(".util-item").forEach((el) => {
    el.addEventListener("click", async () => {
      const e = events[+el.dataset.i];
      let mr = state.roundMeta;
      if (!mr) {
        try { mr = await loadRound(state.current); state.roundMeta = mr; }
        catch { return; }
      }
      if (!state.showUtils) { state.showUtils = true; syncToggle($("utilToggle"), true); }
      state.utilHighlight = Array.isArray(e.det_pos) ? { x: e.det_pos[0], y: e.det_pos[1], letter: (UTIL_STYLE[e.type] || ["", "?"])[1] } : null;
      jumpTo(Math.min(e.tick, mr.max_tick));
      $("utilList").querySelectorAll(".util-item").forEach((x) => x.classList.remove("picked"));
      el.classList.add("picked");
    });
  });
}

/* ---------------- 经济面板 ---------------- */
function renderEconomy(rd) {
  const e = rd.economy;
  if (!e) { $("ecoPanel").innerHTML = '<div class="utility-empty">本回合无经济数据</div>'; return; }
  const wpn = WEAPON_DISPLAY[e.primary] || (rd.round_label === "手枪局" ? "默认手枪" : "—");
  const income = [
    ["回合胜利奖励", e.win_reward], ["击杀奖励", e.kill_reward],
    ["下包个人奖励", e.plant_personal], ["拆包个人奖励", e.defuse_personal],
    ["下包仍输·全队补偿", e.plant_loss_team], ["CT击杀·全队奖励", e.ct_kill_team],
    ["连败补偿", e.loss_bonus],
  ].filter(([, v]) => v > 0);
  const incomeHtml = income.length
    ? income.map(([k, v]) => `<div class="eco-row"><span>${k}</span><b>+$${v}</b></div>`).join("")
    : '<div class="eco-row"><span>本回合无额外收入</span><b>$0</b></div>';
  const syncWarn = (e.tier !== "eco" && e.team_eco >= 3)
    ? '<div class="eco-warn">⚠ 队友大多在 eco，你却起枪——经济脱节。</div>'
    : (e.tier === "eco" && e.team_full >= 3)
      ? '<div class="eco-warn">⚠ 队友全起你却纯经济——正面少一条枪。</div>' : "";
  const advHtml = (e.advice || []).length
    ? `<div class="eco-advice">${e.advice.map((a) => `<p>${colorize(a)}</p>`).join("")}</div>` : "";
  $("ecoPanel").innerHTML = `
    <div class="eco-top">
      <span class="eco-tier ${TIER_CLS[e.tier]}">${e.tier_label}</span>
      <span class="eco-wpn">${esc(wpn)}</span>
      <span class="eco-score">比分 ${e.score_after.T}:${e.score_after.CT}</span>
    </div>
    <div class="eco-grid">
      <div class="eco-cell"><span>本回合消费</span><b>$${e.spend}</b></div>
      <div class="eco-cell"><span>买枪后存款</span><b>$${e.freeze_balance}</b></div>
      <div class="eco-cell"><span>回合结束存款</span><b>$${e.end_balance}</b></div>
      <div class="eco-cell"><span>护甲 / 道具</span><b>${e.armor ? "有甲" : "无甲"} / ${e.util} 件</b></div>
    </div>
    <h3>本回合收入</h3>
    ${incomeHtml}
    <div class="eco-row loss"><span>阵亡装备损失（估算）</span><b>${e.equip_lost ? "-$" + e.equip_lost : "存活保枪 $0"}</b></div>
    <h3>团队起枪（${e.mates} 名队友）</h3>
    <div class="eco-team">全起 ${e.team_full} · 半起/强起 ${e.mates - e.team_full - e.team_eco} · 纯经济 ${e.team_eco}</div>
    ${syncWarn}
    <div class="eco-lossbonus">连败计数 ${e.loss_counter} · 若下回合再输可拿 <b>$${e.next_loss_bonus}</b></div>
    ${advHtml}
    <div class="eco-note">起枪档位按冻结期末主武器+消费近似判断；装备损失按本回合消费估算。规则为 MR12 竞技现行版本。</div>`;
}

function renderComments(rd) {
  const fill = (id, arr) =>
    $(id).innerHTML = arr.length ? arr.map((x) => `<li>${colorize(x)}</li>`).join("")
      : '<li class="empty">无</li>';
  fill("goodList", rd.good_points);
  fill("issueList", rd.issues);
  fill("betterList", rd.better_options);
}

/* ---------------- 侧栏选项卡 ---------------- */
document.querySelectorAll("#sideTabs .tab").forEach((b) => {
  b.addEventListener("click", () => {
    state.activeTab = b.dataset.tab;
    document.querySelectorAll("#sideTabs .tab").forEach((x) =>
      x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab-panel").forEach((p) =>
      p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
    if (b.dataset.tab === "aim") updateAimWindow();
  });
});

/* ---------------- 地图 ---------------- */
// CS2 官方 dust2 雷达坐标：1024px 对应 pos_x=-2476, pos_y=3239, scale=4.4。
function transform() {
  const cv = $("mapCanvas");
  const kx = cv.width / 1024, ky = cv.height / 1024;
  return {
    tx: (x) => ((x + 2476) / 4.4) * kx,
    ty: (y) => ((3239 - y) / 4.4) * ky,
  };
}

const hasPos = (p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]);

function nearestPoint(pts, tick) {
  if (!pts || !pts.length) return null;
  let lo = 0, hi = pts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (pts[mid][3] < tick) lo = mid + 1; else hi = mid;
  }
  if (lo > 0 && Math.abs(pts[lo - 1][3] - tick) < Math.abs(pts[lo][3] - tick)) lo--;
  return pts[lo];
}

function wallAtWorld(x, y) {
  if (!wallData) return false;
  const px = Math.round((x + 2476) / 4.4);
  const py = Math.round((3239 - y) / 4.4);
  if (px < 0 || py < 0 || px >= 1024 || py >= 1024) return true;
  return wallData[(py * 1024 + px) * 4 + 3] > 20;
}

function castRay(x, y, yaw, maxUnits = 1800) {
  const rad = Math.PI * yaw / 180;
  const dx = Math.cos(rad), dy = Math.sin(rad);
  // 与后端 mapvision 一致：跳过起点前 40 单位，避免站在掩膜边缘像素时误报贴墙。
  const startDist = 40;
  const step = 8;
  for (let d = startDist; d <= maxUnits; d += step) {
    if (wallAtWorld(x + dx * d, y + dy * d)) {
      return { dist: d, x: x + dx * d, y: y + dy * d, hit: true };
    }
  }
  return { dist: maxUnits, x: x + dx * maxUnits, y: y + dy * maxUnits, hit: false };
}

function drawArrow(ctx, X, Y, yaw, color, size = 12) {
  const rad = Math.PI * yaw / 180;
  const dx = Math.cos(rad), dy = -Math.sin(rad);
  const tx = X + dx * size, ty = Y + dy * size;
  const px = -dy, py = dx;
  ctx.strokeStyle = color; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(X, Y); ctx.lineTo(tx, ty); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(tx, ty);
  ctx.lineTo(tx - dx * 5 + px * 3, ty - dy * 5 + py * 3);
  ctx.lineTo(tx - dx * 5 - px * 3, ty - dy * 5 - py * 3);
  ctx.closePath(); ctx.fillStyle = color; ctx.fill();
}

function drawMap(tick) {
  const cv = $("mapCanvas"), ctx = cv.getContext("2d");
  const mr = state.roundMeta;
  if (!state.result || !mr) return;
  const { tx, ty } = transform();
  const player = state.result.player;
  const sideMap = Object.fromEntries(mr.roster.map((r) => [r.name, r.side]));
  const sideColor = (nm) => sideMap[nm] === "CT" ? CT_COLOR : T_COLOR;

  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.fillStyle = "#0d1420";
  ctx.fillRect(0, 0, cv.width, cv.height);
  if (radarImage.complete && radarImage.naturalWidth) {
    ctx.globalAlpha = 0.82;
    ctx.drawImage(radarImage, 0, 0, cv.width, cv.height);
    ctx.globalAlpha = 1;
  }

  if (state.showWalls && wallCanvas) {
    ctx.globalAlpha = 0.85;
    ctx.drawImage(wallCanvas, 0, 0, cv.width, cv.height);
    ctx.globalAlpha = 1;
  }

  const keyLabels = new Set(["匪家", "警家", "A包点", "B包点", "A大", "A小", "中路", "B洞", "中门"]);
  ctx.font = "bold 14px Microsoft YaHei";
  ctx.fillStyle = "rgba(225,234,244,0.82)";
  ctx.shadowColor = "#000"; ctx.shadowBlur = 4;
  for (const [name, p] of Object.entries(state.result.landmarks)) {
    if (keyLabels.has(name)) ctx.fillText(name, tx(p[0]) + 8, ty(p[1]) - 8);
  }
  ctx.shadowBlur = 0;

  // 时间窗准星射线 / 弹道射线按 (回合+时间窗) 缓存，避免回放每帧重复 castRay。
  const mePts = mr.trajectories[player] || [];
  const rayKey = `${state.current}|${state.rangeStart}|${state.rangeEnd}`;
  if (!state.rayCache || state.rayCache.key !== rayKey) {
    const aim = [];
    if (state.rangeEnd > state.rangeStart) {
      const rayPts = mePts.filter((p) => p[3] >= state.rangeStart && p[3] <= state.rangeEnd && p[4]);
      for (let i = 0; i < rayPts.length; i += 2) {
        const p = rayPts[i], ray = castRay(p[0], p[1], p[6], 1500);
        aim.push([p[0], p[1], ray.x, ray.y]);
      }
    }
    const bullets = [];
    for (const s of (mr.shots || [])) {
      if (s[1] !== player || s[0] < state.rangeStart || s[0] > state.rangeEnd) continue;
      if (!Number.isFinite(s[3]) || !Number.isFinite(s[4]) || !Number.isFinite(s[5])) continue;
      const ray = castRay(s[3], s[4], s[5], 900);
      bullets.push([s[3], s[4], ray.x, ray.y]);
    }
    state.rayCache = { key: rayKey, aim, bullets };
  }
  if (state.showAim && tick >= state.rangeStart && tick <= state.rangeEnd) {
    ctx.lineWidth = 1; ctx.globalAlpha = 0.14; ctx.strokeStyle = AIM_COLOR;
    for (const [ax, ay, bx, by] of state.rayCache.aim) {
      ctx.beginPath(); ctx.moveTo(tx(ax), ty(ay)); ctx.lineTo(tx(bx), ty(by)); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // 已走过的轨迹；未走过的后段只留极淡的影子。你=金色，队友/敌人=阵营色。
  if (state.showTrails) {
    for (const [nm, pts] of Object.entries(mr.trajectories)) {
      const isMe = nm === player;
      const color = isMe ? ME_COLOR : sideColor(nm);
      ctx.globalAlpha = isMe ? 0.2 : 0.08;
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      pts.forEach((p, i) => {
        const X = tx(p[0]), Y = ty(p[1]);
        i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
      });
      ctx.stroke();

      const traveled = pts.filter((p) => p[3] <= tick);
      if (traveled.length > 1) {
        ctx.globalAlpha = isMe ? 0.95 : 0.42;
        ctx.lineWidth = isMe ? 2.3 : 1.2;
        ctx.beginPath();
        traveled.forEach((p, i) => {
          const X = tx(p[0]), Y = ty(p[1]);
          i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
        });
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
  }

  // 炸弹
  const bomb = mr.bomb;
  if (bomb.plant_pos && hasPos(bomb.plant_pos) && tick >= (bomb.plant_tick || 0)) {
    const X = tx(bomb.plant_pos[0]), Y = ty(bomb.plant_pos[1]);
    ctx.fillStyle = "#ffd166";
    ctx.fillRect(X - 5, Y - 5, 10, 10);
    ctx.font = "10px Microsoft YaHei";
    ctx.fillText(bomb.site + "区下包", X + 8, Y);
  }

  // 死亡点：你=金色×，其他=阵营色×
  for (const d of mr.deaths) {
    if (!hasPos(d.victim_pos) || d.tick > tick) continue;
    const X = tx(d.victim_pos[0]), Y = ty(d.victim_pos[1]);
    ctx.strokeStyle = d.victim === player ? ME_COLOR : sideColor(d.victim);
    ctx.lineWidth = d.victim === player ? 2.2 : 1.6;
    ctx.beginPath();
    ctx.moveTo(X - 5, Y - 5); ctx.lineTo(X + 5, Y + 5);
    ctx.moveTo(X + 5, Y - 5); ctx.lineTo(X - 5, Y + 5);
    ctx.stroke();
  }

  // 已爆开的道具落点：烟/闪/雷/火/诱
  if (state.showUtils) {
    for (const u of (mr.utilities || [])) {
      if (u.tick > tick || !hasPos(u.det_pos)) continue;
      const X = tx(u.det_pos[0]), Y = ty(u.det_pos[1]);
      const [color, letter] = UTIL_STYLE[u.type] || ["#b388ff", "?"];
      ctx.beginPath(); ctx.arc(X, Y, 9, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.globalAlpha = 0.9; ctx.fill(); ctx.globalAlpha = 1;
      ctx.strokeStyle = u.side === "CT" ? CT_COLOR : T_COLOR;
      ctx.lineWidth = 2; ctx.stroke();
      ctx.fillStyle = "#10151d"; ctx.font = "bold 10px Microsoft YaHei";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(letter, X, Y + 0.5);
      ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
    }
  }

  // 道具定位高亮（点击右侧道具列表触发）
  if (state.utilHighlight) {
    const X = tx(state.utilHighlight.x), Y = ty(state.utilHighlight.y);
    ctx.strokeStyle = ME_COLOR; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(X, Y, 15, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.arc(X, Y, 22, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(9,16,24,.85)";
    ctx.fillRect(X + 24, Y - 22, 76, 18);
    ctx.fillStyle = ME_COLOR; ctx.font = "bold 11px Microsoft YaHei";
    ctx.fillText("已定位道具", X + 28, Y - 9);
  }

  // 开火弹道：最近 1.5 秒全队弹道 + 时间窗内你的所有开火方向。
  if (state.showBullets) {
    ctx.lineWidth = 1; ctx.globalAlpha = 0.16; ctx.strokeStyle = BULLET_COLOR;
    for (const [ax, ay, bx, by] of state.rayCache.bullets) {
      ctx.beginPath(); ctx.moveTo(tx(ax), ty(ay)); ctx.lineTo(tx(bx), ty(by)); ctx.stroke();
    }
    const recent = (mr.shots || []).filter((s) => s[0] <= tick && s[0] >= tick - 96 &&
      Number.isFinite(s[3]) && Number.isFinite(s[4]) && Number.isFinite(s[5]));
    ctx.globalAlpha = 0.95;
    for (const s of recent) {
      const ray = castRay(s[3], s[4], s[5], 1000);
      ctx.strokeStyle = s[1] === player ? BULLET_COLOR : (s[2] === "CT" ? "#8db9ff" : "#ff9d9d");
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(tx(s[3]), ty(s[4]));
      ctx.lineTo(tx(ray.x), ty(ray.y)); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // 当前时刻全员位置、人物朝向。你=金圈+“你”标签。
  for (const [nm, pts] of Object.entries(mr.trajectories)) {
    const p = nearestPoint(pts, tick);
    if (!p || !p[4]) continue;
    const X = tx(p[0]), Y = ty(p[1]);
    const isMe = nm === player;
    const color = sideColor(nm);
    ctx.beginPath(); ctx.arc(X, Y, isMe ? 7 : 5, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    if (isMe) {
      ctx.strokeStyle = ME_COLOR; ctx.lineWidth = 2.5; ctx.stroke();
      ctx.fillStyle = ME_COLOR; ctx.font = "bold 14px Microsoft YaHei";
      ctx.shadowColor = "#000"; ctx.shadowBlur = 3;
      ctx.fillText("你", X - 6, Y - 12);
      ctx.shadowBlur = 0;
    }
    drawArrow(ctx, X, Y, p[6], isMe ? ME_COLOR : color, isMe ? 16 : 11);
  }

  // 你当前准星：到第一道墙的视线 + 90° 可视扇形。
  const me = nearestPoint(mePts, tick);
  if (state.showAim && me && me[4]) {
    const sx = tx(me[0]), sy = ty(me[1]);
    ctx.save();
    ctx.globalAlpha = 0.08; ctx.fillStyle = AIM_COLOR;
    ctx.beginPath(); ctx.moveTo(sx, sy);
    const fanYaw = me[6];
    for (let a = fanYaw - 45; a <= fanYaw + 45; a += 6) {
      const ray = castRay(me[0], me[1], a, 1600);
      ctx.lineTo(tx(ray.x), ty(ray.y));
    }
    ctx.lineTo(sx, sy); ctx.fill();
    ctx.restore();

    const ray = castRay(me[0], me[1], fanYaw, 1800);
    ctx.strokeStyle = AIM_COLOR; ctx.lineWidth = 1.8;
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(tx(ray.x), ty(ray.y)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(tx(ray.x), ty(ray.y), 5, 0, Math.PI * 2);
    ctx.strokeStyle = AIM_COLOR; ctx.lineWidth = 2; ctx.stroke();
    if (ray.dist < 1800) {
      ctx.fillStyle = "rgba(9,16,24,.8)";
      ctx.fillRect(tx(ray.x) + 7, ty(ray.y) - 10, 72, 18);
      ctx.fillStyle = AIM_COLOR; ctx.font = "bold 11px Consolas";
      ctx.fillText("墙 " + Math.round(ray.dist / 40 * 10) / 10 + "m", tx(ray.x) + 11, ty(ray.y) + 3);
    }
  }

  $("mapClock").textContent = Math.max(0, (tick - mr.live_start) / 64).toFixed(1) + "s";
}

function setupSlider(mr) {
  const slider = $("timeSlider");
  slider.min = mr.live_start;
  slider.max = mr.max_tick;
  slider.step = 1;
  slider.value = mr.live_start;
  drawMap(mr.live_start);
  slider.oninput = () => drawMap(+slider.value);
}

function jumpTo(tick) {
  const slider = $("timeSlider");
  slider.value = tick;
  drawMap(tick);
}

/* 图层开关 */
function syncToggle(btn, on) { btn.classList.toggle("on", on); }
$("trailToggle").addEventListener("click", () => {
  state.showTrails = !state.showTrails; syncToggle($("trailToggle"), state.showTrails);
  drawMap(+$("timeSlider").value);
});
$("wallToggle").addEventListener("click", () => {
  state.showWalls = !state.showWalls; syncToggle($("wallToggle"), state.showWalls);
  drawMap(+$("timeSlider").value);
});
$("utilToggle").addEventListener("click", () => {
  state.showUtils = !state.showUtils; syncToggle($("utilToggle"), state.showUtils);
  drawMap(+$("timeSlider").value);
});
$("aimToggle").addEventListener("click", () => {
  state.showAim = !state.showAim; syncToggle($("aimToggle"), state.showAim);
  drawMap(+$("timeSlider").value);
});
$("bulletToggle").addEventListener("click", () => {
  state.showBullets = !state.showBullets; syncToggle($("bulletToggle"), state.showBullets);
  drawMap(+$("timeSlider").value);
});

/* 1:1 实时回放（可选倍速） */
$("speedSel").addEventListener("change", () => { state.speed = +$("speedSel").value; });
$("playBtn").addEventListener("click", () => {
  if (state.playing) { stopPlay(); return; }
  const slider = $("timeSlider");
  if (+slider.value >= +slider.max) slider.value = +slider.min;
  state.playing = true;
  state.playLast = performance.now();
  $("playBtn").textContent = "■ 停止";
  state.playTimer = setInterval(() => {
    const now = performance.now();
    const dt = Math.min(now - state.playLast, 250) / 1000;
    state.playLast = now;
    let v = +slider.value + dt * 64 * state.speed;   // 64 tick = 1 秒
    if (v >= +slider.max) { v = +slider.max; stopPlay(); }
    slider.value = v;
    drawMap(v);
  }, 40);
});

function stopPlay() {
  state.playing = false;
  if (state.playTimer) clearInterval(state.playTimer);
  state.playTimer = null;
  $("playBtn").textContent = "▶ 回放";
}

/* 快捷键：空格播放/暂停，←/→ ±1s，Shift ±5s */
document.addEventListener("keydown", (e) => {
  if (!state.result || $("report").classList.contains("hidden")) return;
  const tag = (e.target.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  const mr = state.roundMeta;
  if (!mr) return;
  if (e.code === "Space") { e.preventDefault(); $("playBtn").click(); }
  else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
    e.preventDefault();
    const step = (e.shiftKey ? 5 : 1) * 64 * (e.code === "ArrowLeft" ? -1 : 1);
    jumpTo(Math.max(mr.live_start, Math.min(mr.max_tick, +$("timeSlider").value + step)));
  }
});

/* ---------------- 时间窗准星 ---------------- */
function setupAimRange(rd, mr) {
  const startEl = $("rangeStart"), endEl = $("rangeEnd");
  startEl.min = endEl.min = mr.live_start;
  startEl.max = endEl.max = mr.max_tick;
  startEl.step = endEl.step = 1;
  let start = mr.live_start, endC = mr.end_tick || mr.max_tick;
  if (rd.death_review) {
    endC = Math.max(mr.live_start + 64, Math.min(mr.max_tick, mr.live_start + Math.round(rd.death_review.time * 64)));
    start = Math.max(mr.live_start, endC - 640);
  } else {
    start = Math.max(mr.live_start, endC - 10 * 64);
  }
  state.rangeStart = start;
  state.rangeEnd = endC;
  startEl.value = start; endEl.value = endC;
  startEl.oninput = () => {
    state.rangeStart = Math.min(+startEl.value, state.rangeEnd - 16);
    startEl.value = state.rangeStart;
    updateAimWindow();
  };
  endEl.oninput = () => {
    state.rangeEnd = Math.max(+endEl.value, state.rangeStart + 16);
    endEl.value = state.rangeEnd;
    updateAimWindow();
  };
  // 默认把地图停在死亡瞬间（或回合结束），让准星线和时间窗对准同一段画面。
  $("timeSlider").value = endC;
  updateAimWindow();
}

$("deathWindowBtn").addEventListener("click", () => {
  const rd = state.result.rounds.find((r) => r.round_index === state.current);
  const mr = state.roundMeta;
  if (!rd || !mr) return;
  const end = rd.death_review
    ? mr.live_start + Math.round(rd.death_review.time * 64)
    : (mr.end_tick || mr.max_tick);
  const start = Math.max(mr.live_start, end - 640);
  state.rangeStart = start; state.rangeEnd = Math.min(end, mr.max_tick);
  $("rangeStart").value = state.rangeStart;
  $("rangeEnd").value = state.rangeEnd;
  updateAimWindow();
});

function updateAimWindow() {
  const mr = state.roundMeta;
  if (!mr) return;
  const player = state.result.player;
  const pts = (mr.trajectories[player] || []).filter((p) =>
    p[3] >= state.rangeStart && p[3] <= state.rangeEnd && p[4]);
  const dur = Math.max(0, (state.rangeEnd - state.rangeStart) / 64);
  $("rangeText").textContent =
    `${Math.max(0, (state.rangeStart - mr.live_start) / 64).toFixed(1)}s – ${Math.max(0, (state.rangeEnd - mr.live_start) / 64).toFixed(1)}s`;
  $("rangeStats").textContent = `${pts.length} 个采样 · ${dur.toFixed(1)}s`;

  let head = 0, badPitch = 0, wallHug = 0, covered = new Set(), wallRays = 0;
  const checked = Math.max(1, Math.ceil(pts.length / 4));
  const counts = new Array(24).fill(0);
  const pitches = new Array(24).fill(0);
  pts.forEach((p, i) => {
    if (Math.abs(p[5]) <= 6) head++;
    if (p[5] > 15 || p[5] < -15) badPitch++;
    const yaw = ((p[6] % 360) + 360) % 360;
    const bin = Math.floor(yaw / 15) % 24;
    covered.add(bin);
    counts[bin]++; pitches[bin] += p[5];
    if (i % 4 === 0) {
      const ray = castRay(p[0], p[1], p[6], 1200);
      if (ray.hit) wallRays++;
      if (ray.hit && ray.dist <= 80) wallHug++;
    }
  });
  const n = Math.max(pts.length, 1);
  const headPct = Math.round(head / n * 100);
  const wallRayPct = Math.round(wallRays / checked * 100);
  const wallHugPct = Math.round(wallHug / checked * 100);
  const blind = 24 - covered.size;

  state.roseBins = { counts, pitches, total: pts.length };

  // 人话总结：先说结论，再给证据。
  const sentences = [];
  sentences.push(headPct >= 75
    ? `这 ${dur.toFixed(0)} 秒里你有 ${headPct}% 的时间准星保持在头线高度，遇到人可以直接开火。`
    : `这 ${dur.toFixed(0)} 秒里只有 ${headPct}% 的时间准星在头线高度——遇到人要先抬枪，会慢半拍。`);
  const order = counts.map((c, i) => [c, i]).sort((a, b) => b[0] - a[0]);
  if (order[0][0] > 0) {
    // 按 8 个方位聚合，避免相邻 15° 格报出两个相同的方位名。
    const sectors = new Array(8).fill(0);
    counts.forEach((c, i) => { sectors[Math.floor(i / 3) % 8] += c; });
    const top = sectors.map((c, k) => [c, k]).sort((a, b) => b[0] - a[0])
      .slice(0, 2).filter((x) => x[0] > 0);
    const pct = Math.round(top.reduce((s, x) => s + x[0], 0) / n * 100);
    const dirs = top.map((x) => DIR_NAMES[x[1]]).join("和");
    sentences.push(`准星主要停在${dirs}方向（合计 ${pct}% 的采样）。`);
  }
  if (blind > 0) {
    // 找最大的连续空档，指出最危险的未看方向。
    let bestLen = 0, bestStart = 0, cur = 0, start = 0;
    for (let i = 0; i < 48; i++) {
      if (counts[i % 24] === 0) { if (cur === 0) start = i; cur++; if (cur > bestLen) { bestLen = cur; bestStart = start; } }
      else cur = 0;
    }
    if (bestLen >= 2) {
      const midYaw = ((bestStart + bestLen / 2) % 24) * 15;
      sentences.push(`有 ${blind} 个 15° 方向整段没看，最大空档在${dirName(midYaw)}侧——如果这是架枪位，让队友帮你看住那边。`);
    }
  }
  if (wallHugPct > 35) sentences.push(`${wallHugPct}% 的采样准星贴着 2 米内的墙，敌人拉出时你要先修准星；建议离墙半个到一个身位。`);
  if (badPitch > 0) sentences.push(`另有 ${badPitch} 帧明显看地/看天（俯仰 >15°），移动时记得把头线带上。`);
  $("aimSummary").innerHTML = sentences.map((s) => `<p>${s}</p>`).join("");

  const badgeCls = (headPct < 55 || wallHugPct > 35) ? "warn" : "";
  $("aimInfo").innerHTML = `
    <div class="arow" title="无接触时俯仰角≤6°的比例，即准星放在敌人头部高度的时间占比"><span>头线率（准星在头高的时间）</span><b>${headPct}%</b></div>
    <div class="arow" title="采样射线在1200单位内遇到墙体的比例"><span>视线遇墙（1200 单位内）</span><b>${wallRayPct}%</b></div>
    <div class="arow" title="射线在80单位（约2米）内撞墙的比例"><span>准星贴脸墙（&lt;2m）</span><b>${wallHugPct}%</b></div>
    <div class="arow"><span>俯仰异常（&gt;15°）</span><b>${badPitch} 帧</b></div>
    <div class="arow"><span>看过的方向（15°一格）</span><b>${covered.size}/24</b></div>
    <div style="margin-top:8px">
      <span class="aim-badge ${badgeCls}">${headPct >= 75 ? "头线稳定" : "头线偏低"}</span>
      <span class="aim-badge ${wallHugPct > 35 ? "warn" : ""}">${wallHugPct > 35 ? "近墙过多" : "离墙合理"}</span>
      <span class="aim-badge">${blind > 12 ? "观察方向偏窄" : "方向覆盖正常"}</span>
    </div>`;
  drawAimRose(pts, headPct, wallHugPct);
  drawMap(+$("timeSlider").value || state.rangeStart);
}

function drawAimRose(pts, headPct, wallHugPct) {
  const cv = $("aimCanvas"), ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height, cx = W / 2, cy = H / 2, R = W * 0.34;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#0d1420"; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "#263449"; ctx.lineWidth = 1;
  for (const r of [0.33, 0.66, 1]) {
    ctx.beginPath(); ctx.arc(cx, cy, R * r, 0, Math.PI * 2); ctx.stroke();
  }
  for (let a = 0; a < 360; a += 30) {
    const rad = (a - 90) * Math.PI / 180;
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(rad) * R, cy + Math.sin(rad) * R); ctx.stroke();
  }
  const counts = new Array(24).fill(0);
  const pitches = new Array(24).fill(0);
  pts.forEach((p) => {
    const yaw = ((p[6] % 360) + 360) % 360;
    const i = Math.floor(yaw / 15) % 24;
    counts[i]++; pitches[i] += p[5];
  });
  const maxC = Math.max(1, ...counts);
  for (let i = 0; i < 24; i++) {
    // 世界 yaw 0 指向 +x（雷达右侧），屏幕上 y 轴向下，因此屏幕极角 = -yaw。
    const start = -((i + 1) * 15) * Math.PI / 180;
    const end = -(i * 15) * Math.PI / 180;
    const rad = (counts[i] / maxC) * R * 0.95;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, rad, start, end);
    ctx.closePath();
    if (counts[i]) {
      const avgPitch = pitches[i] / counts[i];
      ctx.fillStyle = Math.abs(avgPitch) > 15 ? "rgba(255,107,107,.45)" :
        (counts[i] > maxC * 0.55 ? "rgba(111,255,233,.6)" : "rgba(111,255,233,.25)");
    } else {
      ctx.fillStyle = "rgba(255,107,107,.10)";
    }
    ctx.fill();
  }
  // 八方位标签：与地图方位一致（右=地图右方）。
  ctx.fillStyle = "#c2ccda"; ctx.font = "bold 15px Microsoft YaHei";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (let k = 0; k < 8; k++) {
    const yaw = k * 45;
    const rad = -yaw * Math.PI / 180;
    ctx.fillText(DIR_NAMES[k], cx + Math.cos(rad) * (R + 22), cy + Math.sin(rad) * (R + 22));
  }
  // 中心：主看方向
  const top = counts.indexOf(Math.max(...counts));
  if (Math.max(...counts) > 0) {
    ctx.fillStyle = "rgba(9,16,24,.85)";
    ctx.beginPath(); ctx.arc(cx, cy, 26, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = AIM_COLOR; ctx.font = "bold 14px Microsoft YaHei";
    ctx.fillText("主看", cx, cy - 8);
    ctx.fillText(dirName(top * 15 + 7.5), cx, cy + 8);
  }
  ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "#8a97a8"; ctx.font = "13px Microsoft YaHei";
  ctx.fillText("方位与地图一致", 10, 16);
  ctx.fillStyle = AIM_COLOR; ctx.font = "bold 14px Microsoft YaHei";
  ctx.fillText(`头线 ${headPct}% · 近墙 ${wallHugPct}%`, 10, H - 10);
  // 窗口末准星方向
  ctx.strokeStyle = "#ffd166"; ctx.lineWidth = 2;
  const last = pts.length ? pts[pts.length - 1] : null;
  if (last) {
    const rad = (-last[6]) * Math.PI / 180;
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(rad) * R, cy + Math.sin(rad) * R); ctx.stroke();
  }
}

/* 玫瑰图悬停提示 */
$("aimCanvas").addEventListener("mousemove", (e) => {
  const b = state.roseBins;
  const tip = $("roseTip");
  if (!b || !b.total) { tip.classList.add("hidden"); return; }
  const cv = $("aimCanvas"), rect = cv.getBoundingClientRect();
  const sx = (e.clientX - rect.left) * (cv.width / rect.width);
  const sy = (e.clientY - rect.top) * (cv.height / rect.height);
  const cx = cv.width / 2, cy = cv.height / 2, R = cv.width * 0.34;
  const dx = sx - cx, dy = sy - cy;
  const dist = Math.hypot(dx, dy);
  if (dist > R + 26) { tip.classList.add("hidden"); return; }
  const yaw = ((-Math.atan2(dy, dx) * 180 / Math.PI) % 360 + 360) % 360;
  const i = Math.floor(yaw / 15) % 24;
  const c = b.counts[i];
  const pct = Math.round(c / b.total * 100);
  const avgP = c ? (b.pitches[i] / c).toFixed(1) : "—";
  tip.innerHTML = `<b>${dirName(i * 15 + 7.5)}（${i * 15}°–${(i + 1) * 15}°）</b><br>
    采样 ${c} 次 · 占 ${pct}%<br>平均俯仰 ${avgP}°${c === 0 ? "<br><span class='warn'>这个方向一次没看</span>" : ""}`;
  tip.classList.remove("hidden");
  const wrap = cv.parentElement.getBoundingClientRect();
  tip.style.left = Math.min(e.clientX - wrap.left + 14, wrap.width - 150) + "px";
  tip.style.top = (e.clientY - wrap.top + 10) + "px";
});
$("aimCanvas").addEventListener("mouseleave", () => $("roseTip").classList.add("hidden"));