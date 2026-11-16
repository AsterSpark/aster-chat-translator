/* ============================================================
   设置面板（独立窗口）
   - 打开时向后端要当前配置
   - 保存后后端写 config.json 并重绑热键
   - 缩放显示值沿用旧映射：显示 = 100 + (scale-1)*500（显示 125 = 实际 1.05×）
   ============================================================ */


let scaleNow = 1;

const KEY_LABEL = {
  num_add: "小键盘 +", num_sub: "小键盘 −", num_mul: "小键盘 *", num_div: "小键盘 /",
  num_dec: "小键盘 .", num_enter: "小键盘 Enter",
  num0: "小键盘 0", num1: "小键盘 1", num2: "小键盘 2", num3: "小键盘 3", num4: "小键盘 4",
  num5: "小键盘 5", num6: "小键盘 6", num7: "小键盘 7", num8: "小键盘 8", num9: "小键盘 9",
  insert: "Insert", home: "Home", end: "End", pgup: "PageUp", pgdn: "PageDown",
};
for (let i = 1; i <= 12; i++) KEY_LABEL["f" + i] = "F" + i;

const CODE_MAP = {
  NumpadAdd: "num_add", NumpadSubtract: "num_sub", NumpadMultiply: "num_mul",
  NumpadDivide: "num_div", NumpadDecimal: "num_dec", NumpadEnter: "num_enter",
  Insert: "insert", Home: "home", End: "end", PageUp: "pgup", PageDown: "pgdn",
};
for (let i = 0; i <= 9; i++) CODE_MAP["Numpad" + i] = "num" + i;
for (let i = 1; i <= 12; i++) CODE_MAP["F" + i] = "f" + i;

const FONT_CANDIDATES = [
  "Noto Sans SC", "Noto Sans SC Medium", "Microsoft YaHei UI", "Microsoft YaHei",
  "微软雅黑", "猫啃什锦黑", "等线", "黑体", "SimHei", "Segoe UI", "Cascadia Mono",
];

const EYE_OPEN = '<svg width="15" height="15" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><path d="M1.1 7S3.3 3.5 7 3.5 12.9 7 12.9 7 10.7 10.5 7 10.5 1.1 7 1.1 7z"/><circle cx="7" cy="7" r="1.6"/></svg>';
const EYE_SHUT = '<svg width="15" height="15" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"><path d="M1.1 7S3.3 3.5 7 3.5 12.9 7 12.9 7 10.7 10.5 7 10.5 1.1 7 1.1 7z"/><path d="M2.2 11.8L11.8 2.2"/></svg>';

let cfg = null;
let ws = null;

/* ---------------- 工具 ---------------- */
function setStatus(text, kind) {
  const el = $("sstatus");
  el.textContent = text || "";
  el.className = "sstatus" + (kind ? " " + kind : "");
}

function fontAvailable(name) {
  try {
    return document.fonts.check(`13px "${name}"`);
  } catch (e) {
    return false;
  }
}

function fillFonts(current) {
  const sel = $("fontSel");
  sel.innerHTML = "";
  const list = FONT_CANDIDATES.filter(fontAvailable);
  if (current && !list.includes(current)) list.unshift(current);
  for (const name of list) {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  }
  sel.value = current || list[0] || "";
}

/* 显示值 ↔ 实际缩放（旧映射，别"修"） */
const scaleToDisplay = (s) => Math.max(0, Math.round((s - 1) * 100) * 5 + 100);
const displayToScale = (d) => Math.round((1 + (Number(d) - 100) / 500) * 100) / 100;

/* 界面缩放：按键式，节点 5 = 1%，默认 100，最低 0（=0.8×） */
const SC_MIN = 0, SC_MAX = 200, SC_STEP = 5;

function paintScaleUI() {
  const d = scaleToDisplay(scaleNow);
  const top = Math.min(SC_MAX, scaleToDisplay(maxScaleAllowed()));
  $("scValue").textContent = String(d);
  $("scMinus").disabled = d <= SC_MIN;
  $("scPlus").disabled = d >= top;
}

function bumpScale(delta) {
  const now = scaleToDisplay(scaleNow);
  const next = Math.max(SC_MIN, Math.min(SC_MAX, now + delta));
  if (next === now) return;
  scaleNow = Math.max(0.8, Math.min(1.2, displayToScale(next)));
  markDirty();
  paintScaleUI();
  paintScale();          // 即时把缩放应用到窗口
  const shown = scaleToDisplay(scaleNow);
  const top = Math.min(SC_MAX, scaleToDisplay(maxScaleAllowed()));
  setStatus(shown >= top
    ? `界面缩放 ${shown}：已到屏幕能放下的上限`
    : `界面缩放 ${shown}（实际 ${Math.round(scaleNow * 100)}%）`);
}

/* 引擎双态：离线词库 / AI 模型（内部仍用 chkAI 表达"是否离线"） */
function paintSeg(offline) {
  $("chkAI").checked = !!offline;
  document.querySelectorAll("#engSeg button").forEach((b) => {
    const isOff = b.dataset.eng === "offline";
    b.classList.toggle("on", isOff === !!offline);
  });
}

function isOffline() {
  return $("chkAI").checked;
}

/* ---------------- 渲染 ---------------- */
// 字体是否处于「系统默认」：ui_font 为空时下拉框会显示第一个可用字体，
// 但保存时要写回空串，否则"恢复默认到系统字体"就变成"选了列表第一个"。
let fontIsSystemDefault = false;

function apply(c) {
  cfg = JSON.parse(JSON.stringify(c));
  fontIsSystemDefault = !cfg.ui_font;
  fillFonts(cfg.ui_font || "");

  scaleNow = Number(cfg.ui_scale) || 1;
  paintScaleUI();
  document.documentElement.style.zoom = scaleNow;
  applyFontToSelf(c);

  const hk = cfg.hotkey || {};
  $("chkGame").checked = cfg.require_game !== false;

  // 引擎：auto 时按有没有 Key 决定初始状态
  const apiCfg = cfg.api || {};
  const wantOffline = cfg.engine === "offline" ||
    (cfg.engine !== "ai" && !apiCfg.key_set);
  paintSeg(!!wantOffline);

  $("keyTranslate").textContent = KEY_LABEL[hk.translate_key] || hk.translate_key || "未设置";
  $("keyLast").textContent = KEY_LABEL[hk.last_key] || hk.last_key || "未设置";

  const keyInput = $("apiKey");
  if (typeof apiCfg.key_value === "string") {
    keyInput.value = apiCfg.key_value;
    keyInput.dataset.original = apiCfg.key_value;
  }
  keyInput.type = "password";
  $("eyeKey").innerHTML = EYE_SHUT;
  keyInput.placeholder = apiCfg.key_set ? "已配置，留空不修改" : "未配置，粘贴 Key 后保存";
}

/* ---------------- 交互：引擎 / 滑杆 / 眼睛 ---------------- */
// 有未保存的改动时，忽略后端广播：保存设置、框选区域都会广播 settings，
// 一冲就会把用户正在改的东西（引擎/缩放/Key）静默抹回旧值。
let formDirty = false;
function markDirty() { formDirty = true; }

document.querySelectorAll("#engSeg button").forEach((b) => {
  b.addEventListener("click", () => {
    paintSeg(b.dataset.eng === "offline");
    markDirty();
    setStatus("引擎已切换，点保存生效");
  });
});

$("scMinus").addEventListener("click", () => bumpScale(-SC_STEP));
$("scPlus").addEventListener("click", () => bumpScale(SC_STEP));

$("apiKey").addEventListener("input", markDirty);

$("chkGame").addEventListener("change", markDirty);

// 用户自己挑字体 = 不再是"系统默认"
$("fontSel").addEventListener("change", () => {
  fontIsSystemDefault = false;
  markDirty();
});

$("eyeKey").addEventListener("click", () => {
  const el = $("apiKey");
  const show = el.type === "password";
  el.type = show ? "text" : "password";
  $("eyeKey").innerHTML = show ? EYE_OPEN : EYE_SHUT;
  el.focus();
  const n = el.value.length;
  try { el.setSelectionRange(n, n); } catch (e) {}
});

$("clearKey").addEventListener("click", () => {
  $("apiKey").value = "";
  cfg._clearKey = true;
  markDirty();
  setStatus("保存后清除 Key");
});

/* ---------------- 改键 ---------------- */
let capture = null;

function startCapture(btn, which) {
  if (capture) capture.classList.remove("capturing");
  capture = btn;
  btn.classList.add("capturing");
  btn.textContent = "按任意键…";
  btn.dataset.capturing = which;
  setStatus("按下想用的键，Esc 取消");
}

function stopCapture() {
  if (!capture) return;
  capture.classList.remove("capturing");
  capture.dataset.capturing = "";
  capture = null;
}

document.addEventListener("keydown", (e) => {
  if (!capture) return;
  e.preventDefault();
  if (e.key === "Escape") {
    const which = capture.dataset.capturing;
    const hk = (cfg && cfg.hotkey) || {};
    capture.textContent = KEY_LABEL[hk[which]] || "未设置";
    stopCapture();
    setStatus("");
    return;
  }
  const name = CODE_MAP[e.code];
  if (!name) {
    setStatus("这个键不支持，换小键盘或 F1–F12", "err");
    return;
  }
  const which = capture.dataset.capturing;
  capture.textContent = KEY_LABEL[name] || name;
  capture.dataset.newValue = name;
  stopCapture();
  setStatus("改好了，记得点保存");
});

$("keyTranslate").addEventListener("click", (e) => startCapture(e.currentTarget, "translate_key"));
$("keyLast").addEventListener("click", (e) => startCapture(e.currentTarget, "last_key"));

/* ---------------- 保存 / 重置 ---------------- */
function collect() {
  const hk = Object.assign({}, (cfg && cfg.hotkey) || {});
  const t = $("keyTranslate").dataset.newValue;
  const l = $("keyLast").dataset.newValue;
  if (t) hk.translate_key = t;
  if (l) hk.last_key = l;

  const out = {
    // 恢复默认到「系统字体」时下拉框会显示列表第一个（因为没有"空"选项），
    // 这里要写回空串，否则"系统默认"会变成"选了 Noto Sans SC"。
    ui_font: fontIsSystemDefault ? "" : ($("fontSel").value || ""),
    ui_scale: scaleNow,
    require_game: !!$("chkGame").checked,
    engine: isOffline() ? "offline" : "ai",
    hotkey: hk,
  };

  const box = $("apiKey");
  const typed = box.value.trim();
  if (cfg && cfg._clearKey) out.api_key_clear = true;
  else if (typed && typed !== (box.dataset.original || "")) out.api_key = typed;
  return out;
}

$("btnSave").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    setStatus("后端没连上", "err");
    return;
  }
  setStatus("保存中…");
  ws.send(JSON.stringify({ type: "settings_save", cfg: collect() }));
});

/* 窗口重新打开时由 Rust 侧调用：丢掉未保存状态，重新拉一次已保存的配置 */
window.__refresh = () => {
  formDirty = false;
  try { ws.send(JSON.stringify({ type: "settings_get" })); } catch (e) {}
};

/* ---------------- 截图区域 / 开机自启 ---------------- */
$("btnPickArea").addEventListener("click", () => {
  if (window.__TAURI_INTERNALS__) {
    window.__TAURI_INTERNALS__.invoke("open_selector");
  } else {
    setStatus("框选只能在外壳程序里用", "err");
  }
});

$("btnShot").addEventListener("click", async () => {
  $("btnShot").textContent = "截图中…";
  try {
    await fetch(API + "/capture_test", { method: "POST" });
    await new Promise((r) => setTimeout(r, 500));
    $("shotPreview").src = API + "/capture_preview.png?t=" + Date.now();
    $("shotRow").hidden = false;
    setTimeout(fitSelf, 120);
  } catch (e) {
  } finally {
    $("btnShot").textContent = "测试截图";
  }
});

$("chkAuto").addEventListener("change", async (e) => {
  const on = e.target.checked;
  if (!window.__TAURI_INTERNALS__) {
    e.target.checked = !on;
    return;
  }
  try {
    await window.__TAURI_INTERNALS__.invoke("set_autostart", { enabled: on });
  } catch (err) {
    e.target.checked = !on;
  }
});

async function loadAutostart() {
  if (!window.__TAURI_INTERNALS__) return;
  try {
    const v = await window.__TAURI_INTERNALS__.invoke("get_autostart");
    $("chkAuto").checked = !!v;
  } catch (e) { /* 忽略 */ }
}

/* ---------------- 关闭 ---------------- */
$("btnCloseSettings").addEventListener("click", () => {
  if (window.__TAURI_INTERNALS__) {
    window.__TAURI_INTERNALS__.invoke("close_self");
  } else {
    window.close();
  }
});

/* ---------------- WS ---------------- */
function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "settings_get" }));
    setStatus("");
  };
  ws.onclose = () => setTimeout(connect, 2000);
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (err) { return; }
    if (msg.type === "settings" && msg.cfg) {
      // 有没保存的改动就别覆盖（见 formDirty 注释）
      if (formDirty) return;
      apply(msg.cfg);
    } else if (msg.type === "settings_saved") {
      if (msg.ok) {
        formDirty = false;
        setStatus("已保存", "ok");
        applyFontToSelf(msg.cfg);
        fitSelf();
      } else {
        setStatus("保存失败：" + (msg.error || "未知"), "err");
      }
    }
  };
}

function applyFontToSelf(c) {
  if (!c) return;
  if (c.ui_font) {
    document.documentElement.style.setProperty(
      "--font", `"${c.ui_font}", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif`);
  }
}

/* ---------------- 尺寸自适应 ---------------- */
function fitSelf() {
  const el = document.getElementById("settings");
  if (!el) return;

  // 先解除上一次的限制，否则量到的是被限制后的高度（会来回抖）
  const sc = document.querySelector(".settings-scroll");
  if (sc) sc.style.maxHeight = "none";

  // 在 100% 下量（zoom 会把量到的数值也放大，再乘一次就翻倍）
  const prev = document.documentElement.style.zoom;
  document.documentElement.style.zoom = "1";
  const contentH = Math.max(el.scrollHeight, el.offsetHeight);
  document.documentElement.style.zoom = prev || String(scaleNow || 1);

  const sc0 = scaleNow || 1;
  const screenH = (window.screen.availHeight || 800);
  const maxWin = Math.floor(screenH * SCREEN_USE_RATIO);  // 上限统一在 common.js
  const barH = document.querySelector(".bar") ? document.querySelector(".bar").offsetHeight : 0;
  const footH = document.querySelector(".foot") ? document.querySelector(".foot").offsetHeight : 0;

  // 能放下就按内容给高度；放不下就让中间区域滚动，窗口高度取 maxWin
  let winH;
  if (contentH > maxWin) {
    const scMax = Math.max(200, maxWin - barH - footH - 2);
    if (sc) sc.style.maxHeight = scMax + "px";
    winH = barH + scMax + footH + 2;
  } else {
    if (sc) sc.style.maxHeight = "none";
    winH = contentH;
  }
  window.__baseH = winH;

  if (window.__TAURI_INTERNALS__ && winH > 120) {
    window.__TAURI_INTERNALS__.invoke("fit_window", {
      width: 560 * sc0,
      height: winH * sc0,
      dpr: window.devicePixelRatio || 1,
    });
  }
}


/* 屏幕放得下的最大缩放（防止窗口超出屏幕） */
function maxScaleAllowed() {
  if (!window.__baseH) return 2;
  const screenH = (window.screen.availHeight || 800) * SCREEN_USE_RATIO;
  return Math.max(0.8, Math.min(2, screenH / (window.__baseH + 10)));
}

/* 调缩放时同步窗口大小 */
function paintScale() {
  if (!window.__baseH) {
    const prev = document.documentElement.style.zoom;
    document.documentElement.style.zoom = "1";
    window.__baseH = document.getElementById("settings").offsetHeight || 640;
    document.documentElement.style.zoom = prev || "1";
  }

  const maxScale = maxScaleAllowed();
  if (scaleNow > maxScale) {
    scaleNow = Math.floor(maxScale * 100) / 100;
    paintScaleUI();
  }

  document.documentElement.style.zoom = scaleNow;
  const sca = document.querySelector(".settings-scroll");
  if (sca) sca.style.maxHeight = "none";

  if (!window.__TAURI_INTERNALS__) return;
  const want = Math.round((window.__baseH + 10) * scaleNow);
  const screenH = (window.screen.availHeight || 800) * SCREEN_USE_RATIO;
  window.__TAURI_INTERNALS__.invoke("fit_window", {
    width: Math.round(560 * scaleNow),
    height: Math.min(want, Math.floor(screenH)),
    dpr: window.devicePixelRatio || 1,
  });
}

/* ---------------- 启动 ---------------- */
connect();
loadAutostart();
window.addEventListener("load", () => {
  paintScaleUI();
  requestAnimationFrame(fitSelf);
  fitSelf();
  [120, 300, 600, 1000].forEach((t) => setTimeout(fitSelf, t));
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => { fitSelf(); setTimeout(fitSelf, 120); });
  }
});

/* ---------------- 顶栏拖动 ---------------- */
document.querySelectorAll(".bar").forEach((bar) => {
  bar.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest("button")) return;
    if (window.__TAURI_INTERNALS__) window.__TAURI_INTERNALS__.invoke("start_drag");
  });
});
