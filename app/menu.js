/* 自绘托盘菜单：动作 → 后端 HTTP / Tauri 命令，点完自己关 */



function closeMenu() {
  invoke("close_self");
}

function post(path) {
  return fetch(API + path, { method: "POST" }).catch(() => {});
}

/* ---------------- 用量：底部一行 ---------------- */
function fmt(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}

function paintUsage(u) {
  const el = $("mStat");
  if (!u || !el) return;

  const calls = u.translates || 0;
  const tokens = (u.prompt || 0) + (u.completion || 0);
  const cny = Number(u.cny || 0);
  const cost = cny > 0 ? (cny < 0.01 ? cny.toFixed(4) : cny.toFixed(2)) : "0";
  // 只有内置了单价的接口才能算钱（目前只有 DeepSeek）。
  // 换了别家还显示 ¥ 的话，那个数字是拿 DeepSeek 的价乘出来的，纯属误导。
  const known = u.priced !== false;

  el.textContent = `本次翻译 ${calls} 次 · ${fmt(tokens)} tokens`
    + (known ? ` · ≈¥${cost}` : "");

  const mdl = u.model ? u.model : "本轮还没调用模型";
  el.title = `${mdl} · 金额按内置价目估算（可在 config.json 的 pricing 里覆盖）`
    + ` · 输入 ${fmt(u.prompt || 0)} / 输出 ${fmt(u.completion || 0)} tokens`
    + (known ? "" : " · 该接口未内置价表，只统计 token");
  fitMenu();
}

/* ---------------- WebSocket：设置 + 用量 ---------------- */
let ws = null;
let retry = null;

function requestUsage() {
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: "usage_get" }));
  } else if (!ws) {
    connectWs();          // 已经在 CONNECTING 就别再建一条（onopen 会自己发）
  }
}

function connectWs() {
  if (retry) { clearTimeout(retry); retry = null; }
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    retry = setTimeout(connectWs, 1200);
    return;
  }
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "settings_get" }));
    ws.send(JSON.stringify({ type: "usage_get" }));
  };
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (err) { return; }
    if (msg.type === "usage") paintUsage(msg.usage);
    if (msg.type === "settings" && msg.cfg) {
      const hk = (msg.cfg.hotkey || {});
      const main = $("mTranslate");
      if (main && hk.translate_key) {
        const k = main.querySelector(".k");
        if (k) k.textContent = pretty(hk.translate_key);
      }
      applyScale(msg.cfg);
      fitMenu();
    }
  };
  ws.onclose = () => { ws = null; retry = setTimeout(connectWs, 2000); };
  ws.onerror = () => {};
}

const KEY_TEXT = {
  num_add: "num +", num_sub: "num −", num_mul: "num *", num_div: "num /",
  num_dec: "num .", num_enter: "num Enter", insert: "Insert", home: "Home",
  end: "End", pgup: "PageUp", pgdn: "PageDown",
};
for (let i = 1; i <= 12; i++) KEY_TEXT["f" + i] = "F" + i;
for (let i = 0; i <= 9; i++) KEY_TEXT["num" + i] = "小键盘 " + i;

function pretty(k) {
  return KEY_TEXT[k] || k;
}

connectWs();
window.addEventListener("focus", requestUsage);

/* ---------------- 菜单项 ---------------- */
$("mTranslate").addEventListener("click", async () => {
  await post("/translate");
  closeMenu();
});

$("mLast").addEventListener("click", async () => {
  await post("/show_last");
  closeMenu();
});

$("mSettings").addEventListener("click", async () => {
  await invoke("open_settings");
  closeMenu();
});

$("mLogs").addEventListener("click", async () => {
  await invoke("open_logs");
  closeMenu();
});

$("mQuit").addEventListener("click", async () => {
  await invoke("quit_app");
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeMenu(); return; }
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  e.preventDefault();
  const items = [...document.querySelectorAll(".mi")];
  if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  // i = -1 表示焦点还在 body 上：上/下都从边缘开始，别落到倒数第二项
  const n = e.key === "ArrowDown"
    ? (i < 0 ? 0 : (i + 1) % items.length)
    : (i <= 0 ? items.length - 1 : i - 1);
  items[n].focus();
  // 菜单项都是 <button>，Enter/Space 的原生激活已经会 click，
  // 以前这里再手动 .click() 会一次触发两遍（翻译发两次请求）
});

/* 跟随全局界面缩放 */
let uiScale = 1;
function applyScale(cfg) {
  const s = Math.min(1.5, Math.max(0.8, Number((cfg || {}).ui_scale) || 1));
  uiScale = s;
  document.documentElement.style.zoom = s;
}

/* 窗口尺寸贴合菜单内容（弹出前就设好，位置才准）
   高度用 offsetHeight：它是布局高度，不会被 html 的 zoom 再乘一次；
   窗口尺寸要乘 uiScale 才是物理像素。 */
function fitMenu() {
  if (!window.__TAURI_INTERNALS__) return;
  const el = document.getElementById("menu");
  if (!el) return;
  const h = Math.ceil(el.offsetHeight || el.scrollHeight) + 2;
  window.__TAURI_INTERNALS__.invoke("fit_window", {
    width: 246 * uiScale, height: h * uiScale,
    dpr: window.devicePixelRatio || 1,
  });
}

fitMenu();
window.addEventListener("load", () => { fitMenu(); setTimeout(fitMenu, 120); });

/* 失焦自动关闭（点外面就收） */
window.addEventListener("blur", () => setTimeout(closeMenu, 80));
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest(".menu")) closeMenu();
});
