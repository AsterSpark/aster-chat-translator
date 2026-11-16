/* 运行日志：轮询 /logs → 解析成 时间 / 级别 / 内容

   - 「清空」记住的是当时最后一行的内容（水位线），之后只显示水位线之后的行
   - 筛选：全部 / 警告 / 错误

   为什么不用"清空时的行号"：/logs 每次只返回最后 500 行，是个滑动窗口，
   行数恒定 500，绝对行号永远追不上；一旦清空就再也刷不出任何新行（实测
   点一次清空后窗口永久空白）。改成按内容找水位线，窗口滑掉了就当作全是新行。
*/

const box = $("logbox");

let timer = null;
let lastText = "";
let filter = "all";
let clearMark = null;       // 清空时最后一行的指纹（t + \0 + m）
let allRows = [];           // [{t, lv, m}]

function rowKey(r) {
  return r.t + "\u0000" + r.m;
}


function levelOf(msg) {
  if (/失败|出错|错误|异常|ERROR|Error|error/.test(msg)) return "err";
  if (/跳过|警告|提醒|WARN|Warn/.test(msg)) return "warn";
  return "info";
}

function parse(text) {
  const out = [];
  for (const raw of text.split("\n")) {
    if (!raw.trim()) continue;
    const m = /^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/.exec(raw);
    const t = m ? m[1] : "";
    const msg = m ? m[2] : raw;
    out.push({ t, lv: levelOf(msg), m: msg });
  }
  return out;
}

function rowHTML(r) {
  const lvText = r.lv === "err" ? "ERROR" : r.lv === "warn" ? "WARN" : "INFO";
  return `<div class="li"><span class="t">${r.t}</span>`
    + `<span class="lv ${r.lv === "info" ? "" : r.lv}">${lvText}</span>`
    + `<span class="m">${escapeHTML(r.m)}</span></div>`;
}

function escapeHTML(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function paint() {
  const shown = allRows.filter((r) => filter === "all" || r.lv === filter);
  if (!shown.length) {
    box.innerHTML = `<div class="li empty-row">没有符合条件的日志</div>`;
    $("count").textContent = `共 0 条`;
    return;
  }
  box.innerHTML = shown.map(rowHTML).join("");
  $("count").textContent = `共 ${shown.length} 条`;
  if ($("chkFollow").checked) box.scrollTop = box.scrollHeight;
}

function ingest(text) {
  if (text === lastText) return;
  lastText = text;
  const stick = $("chkFollow").checked;
  let rows = parse(text);
  if (clearMark) {
    const i = rows.findIndex((r) => rowKey(r) === clearMark);
    // 找到水位线 → 只留它之后的；已被窗口挤掉 → 窗口里全是新的
    rows = i >= 0 ? rows.slice(i + 1) : rows;
  }
  allRows = rows;
  paint();
  if (stick) box.scrollTop = box.scrollHeight;
}

async function load() {
  // 隐藏时不再拉取：窗口只是 hide()，文档不销毁、定时器也不清，否则关了之后
  // 每 1.2s 还在把 500 行日志取回来重新解析一遍。document.hidden 在 Tauri
  // 隐藏窗口时不会变，所以真正的开关由 Rust 侧调 __pause/__resume 控制。
  if (paused || document.hidden) return;
  window.__pollCount = (window.__pollCount || 0) + 1;
  try {
    const r = await fetch(API + "/logs", { cache: "no-store" });
    ingest(await r.text());
  } catch (e) {
    box.innerHTML = `<div class="li empty-row">（连不上后端，日志读不到）</div>`;
    $("count").textContent = "共 0 条";
  }
}

function start() {
  load();
  if (timer) clearInterval(timer);
  timer = setInterval(load, 1200);
}

let paused = false;
window.__pollCount = 0;
window.__isPaused = () => paused;
window.__pause = () => { paused = true; };
window.__resume = () => {
  if (!paused) return;
  paused = false;
  load();
};

// 重新显示时立刻补一次，不用等下一个 1.2s
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) window.__resume();
});

/* ---------- 工具条 ---------- */
document.querySelectorAll(".tchip").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tchip").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    filter = b.dataset.f;
    paint();
  });
});

$("btnClear").addEventListener("click", () => {
  // 记住当前最后一行当水位线；列表本来就是空的就清除水位线（等于不筛）
  clearMark = allRows.length ? rowKey(allRows[allRows.length - 1]) : null;
  allRows = [];
  paint();
});

$("btnOpen").addEventListener("click", async () => {
  try {
    const r = await fetch(API + "/logpath", { cache: "no-store" });
    const j = await r.json();
    if (j && j.path) invoke("open_logfile", { path: j.path });
  } catch (e) { /* 连不上就什么也不做 */ }
});

$("btnCopy").addEventListener("click", () => {
  // 复制"当前看到的那部分"（搜索/筛选后的），而不是全量日志
  const rows = (typeof shown !== "undefined" && shown && shown.length) ? shown : allRows;
  const t = rows.map((r) => (r.t ? `[${r.t}] ` : "") + r.m).join("\n");
  const flash = () => { $("btnCopy").textContent = "已复制"; setTimeout(() => { $("btnCopy").textContent = "复制"; }, 1200); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(t).then(flash).catch(() => {
      const ta = document.createElement("textarea");
      ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); flash(); } catch (e) {}
      document.body.removeChild(ta);
    });
  }
});

$("btnClose").addEventListener("click", () => {
  window.__pause();                 // 关掉就别再轮询了
  if (window.__TAURI_INTERNALS__) window.__TAURI_INTERNALS__.invoke("close_self");
  else window.close();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("btnClose").click();
});

/* 无边框窗口要自己实现拖动：按住标题栏（不按按钮）就交给外壳去拖 */
document.querySelector("#logs .bar").addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  if (e.target.closest("button, a, input, select")) return;
  if (window.__TAURI_INTERNALS__) {
    window.__TAURI_INTERNALS__.invoke("start_drag").catch(() => {});
  }
});

start();

/* 窗口按设计尺寸对齐（Tauri 的 scale 和 WebView2 的 dpr 会不一致，必须自己报）
   尺寸要乘界面缩放，否则 120% 时内容会被窗口裁掉。 */
/* 窗口尺寸交给用户拖：日志窗是可缩放窗口，以前每次换缩放都强制设回 560×560，
   用户拖大/拖小会被立刻打回去。这里只保留缩放（视觉 zoom），尺寸随窗口。 */
/* ---------- 跟随全局缩放 ---------- */
function applyScale(scale) {
  const s0 = Math.min(1.5, Math.max(0.8, Number(scale) || 1));
  document.documentElement.style.zoom = s0;
}

function watchSettings() {
  let w;
  try {
    w = new WebSocket(WS_URL);
  } catch (e) {
    setTimeout(watchSettings, 2000);
    return;
  }
  w.onopen = () => w.send(JSON.stringify({ type: "settings_get" }));
  w.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "settings" && m.cfg) applyScale(m.cfg.ui_scale);
    } catch (err) {}
  };
  // 原来没重连：后端重启后日志窗的缩放就再也不跟着变了
  w.onclose = () => setTimeout(watchSettings, 2000);
}
watchSettings();
