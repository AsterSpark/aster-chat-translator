/* ============================================================
   Aster 聊天翻译 · 前端
   - 数据来源：WebSocket（Python 后端）
   - 直接双击 index.html 时：用 MOCK，方便只调样式
   ============================================================ */


const MOCK = {
  prefix: "上一次翻译 · 2026-11-14 08:09:51",
  raw: [
    "08:08 [1] [Immaculate Magistrate]: SW has a druid trainer right? just IF does not",
    "08:08 [1] [Chef Donnie]: correct",
    "08:08 [1] [Naiturh Face]: he is over by the park, next to the AH",
  ].join("\n"),
  translation: [
    "08:08 Immaculate Magistrate: 暴风城有德鲁伊训练师吧？只有铁炉堡没有",
    "08:08 Chef Donnie: 没错",
    "08:08 Naiturh Face: 德鲁伊训练师在公园那边",
  ].join("\n"),
  summary: "综合频道闲聊，几个玩家在聊德鲁伊训练师位置，不用理会。",
  replies: [],
  need_key: false,
};


/* 一行译文拆成 元信息 + 正文 */
const HEAD = /^(\d{1,2}:\d{2})\s*((?:\[[^\]]{1,18}\]\s*)+)[:：]?\s*(.*)$/;
const HEAD2 = /^((?:\[[^\]]{1,18}\]\s*)+)[:：]?\s*(.*)$/;

/* 当前显示的内容，供「我想说」当语境 */
let current = { raw: "", translation: "" };

function splitLine(line) {
  const m = HEAD.exec(line) || HEAD2.exec(line);
  if (!m) return { meta: "", body: line };
  return { meta: `${m[1]}  ${m[2]}`, body: m[3] };
}

const ENGINE_LABEL = {
  ai: "AI 模型",
  local: "离线词库",
  glossary: "离线词库",
  offline: "离线词库",
};

function showEngine(engine) {
  const el = $("engineNote");
  if (!el) return;
  // 顶部标签跟引擎走：AI 模型 / 离线词库（与设置里的引擎开关一致）
  const text = ENGINE_LABEL[engine] || "离线词库";
  el.hidden = false;
  el.textContent = text;
  el.dataset.engine = ENGINE_LABEL[engine] ? engine : "glossary";
}

let rawResetTimer = null;

/* 鼠标离开译文：撤掉高亮，并把滚动收进"不露垫片"的范围
   —— 首条落在第 1 行、末条落在第 3 行，中间那些本来就在第 2 行、不动 */
const RAW_PAD = 23;

function settleRawScroll() {
  const box = $("rawText");
  if (!box) return;
  const max = box.scrollHeight - box.clientHeight;
  const low = Math.min(RAW_PAD, max);
  const high = Math.max(low, max - RAW_PAD);
  const want = Math.min(Math.max(box.scrollTop, low), high);
  if (Math.abs(want - box.scrollTop) > 0.5) {
    box.scrollTo({ top: want, behavior: "smooth" });
  }
}

function scheduleRawReset() {
  if (rawResetTimer) clearTimeout(rawResetTimer);
  rawResetTimer = setTimeout(() => {
    rawResetTimer = null;
    const box = $("rawText");
    if (!box || box.querySelector(".raw-line.hl")) return;   // 已经指到别的行了
    box.classList.remove("hl-on");
    settleRawScroll();
  }, 120);
}

function highlightRaw(index, on) {
  const box = document.querySelector(".raw");
  const target = document.querySelector(`.raw-line[data-i="${index}"]`);
  // 原文和译文条数不等时可能找不到对应的原文行。这种情况下**不能**整体变暗，
  // 否则会出现"整块变暗却没有任何一条高亮"的怪状态。
  if (box) box.classList.toggle("hl-on", !!on && !!target);
  if (!target) return;
  target.classList.toggle("hl", on);
  if (!on) return;

  // 用 getBoundingClientRect 算，不依赖 offsetTop 的定位祖先
  const b = box.getBoundingClientRect();
  const t = target.getBoundingClientRect();
  const want = box.scrollTop + (t.top - b.top) - (box.clientHeight - t.height) / 2;
  const max = box.scrollHeight - box.clientHeight;
  box.scrollTo({ top: Math.max(0, Math.min(want, max)), behavior: "smooth" });
}

function render(result) {
  showEngine(result.engine);
  current.raw = result.raw || "";
  current.translation = result.translation || "";

  const judgeCard = $("judgeCard");

  if (result.error) {
    $("barTitle").textContent = "翻译失败";
    $("rawText").textContent = "—";
    $("transRows").innerHTML = "";
    if ($("rawTitle")) $("rawTitle").textContent = "原文";
    if ($("transTitle")) $("transTitle").textContent = "翻译";
    judgeCard.hidden = false;
    judgeCard.classList.add("error");
    $("judgeText").textContent = result.error;
    clearReplies();
    return;
  }
  judgeCard.classList.remove("error");

  $("barTitle").textContent = result.prefix || "Aster 聊天翻译";
  const rawBox = $("rawText");
  rawBox.innerHTML = "";
  (result.raw || "—").split("\n").forEach((line, i) => {
    const d = document.createElement("div");
    d.className = "raw-line";
    d.dataset.i = i;
    d.textContent = line || " ";
    rawBox.appendChild(d);
  });

  rawBox.scrollTop = 23;   // 垫片高度：三行铺满，不留空行
  settleRawScroll();

  const rows = $("transRows");
  rows.innerHTML = "";
  rows.onmouseleave = scheduleRawReset;
  const allLines = (result.translation || "—").split("\n").filter((l) => l.trim());
  const lines = allLines.slice(-6);   // 弹窗显示最后 6 条
  const lineOffset = allLines.length - lines.length;

  // 原文卡片只放得下三行，译文最多显示六条 —— 超出的部分是"静默消失"的，
  // 光看卡片根本不知道还有。标题里报总数，至少让人知道该滚动/该悬停。
  const nRaw = (result.raw || "").split("\n").filter((l) => l.trim()).length;
  const rawTitle = $("rawTitle");
  if (rawTitle) rawTitle.textContent = nRaw > 3 ? `原文 · ${nRaw} 条` : "原文";
  const transTitle = $("transTitle");
  if (transTitle) {
    transTitle.textContent = allLines.length > lines.length
      ? `翻译 · ${allLines.length} 条` : "翻译";
  }
  lines.forEach((line, k) => {
    const { meta, body } = splitLine(line);
    const row = document.createElement("div");
    row.className = "row";
    row.dataset.i = lineOffset + k;
    row.addEventListener("mouseenter", () => {
      if (rawResetTimer) { clearTimeout(rawResetTimer); rawResetTimer = null; }
      row.classList.add("hl");
      highlightRaw(lineOffset + k, true);
    });
    row.addEventListener("mouseleave", () => {
      row.classList.remove("hl");
      highlightRaw(lineOffset + k, false);
      scheduleRawReset();
    });
    if (meta) {
      const m = document.createElement("div");
      m.className = "meta";
      m.textContent = meta;
      row.appendChild(m);
    }
    const b = document.createElement("div");
    b.className = "body";
    b.textContent = body;
    row.appendChild(b);
    rows.appendChild(row);
  });

  if (result.summary) {
    judgeCard.hidden = false;
    $("judgeText").textContent = result.summary;
  } else {
    judgeCard.hidden = true;
  }

  // 建议回复只保留给「我想说」用；截图翻译不再显示
  clearReplies();
}

/* ---------------- 建议回复 ---------------- */
function clearReplies() {
  $("replies").innerHTML = "";
  $("replyHead").textContent = "建议回复 · 点一下复制";
  $("replyHead").hidden = false;
  const hint = $("drawerHint");
  if (hint) hint.hidden = false;
  const f = $("floatReplies");
  if (f) f.hidden = true;
  // 圆球常驻，不随清空隐藏
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (e) { console.error(e); }
  document.body.removeChild(ta);
}

function renderReplies(items, title) {
  const box = $("replies");
  box.innerHTML = "";
  if (!items || !items.length) {
    $("replyHead").hidden = true;
    return;
  }
  $("replyHead").textContent = title || "按你的意思 · 点一下复制";
  $("replyHead").hidden = false;
  const hint2 = $("drawerHint");
  if (hint2) hint2.hidden = true;
  const f = $("floatReplies");
  if (f) {
    f.hidden = false;
    f.classList.remove("closed");
  }
  syncBall();

  for (const it of items) {
    const card = document.createElement("div");
    card.className = "reply-card";

    const badge = document.createElement("span");
    badge.className = "reply-copied";
    badge.textContent = "已复制";
    card.appendChild(badge);

    const en = document.createElement("div");
    en.className = "reply-en";
    en.textContent = it.en || "";
    card.appendChild(en);

    if (it.cn) {
      const cn = document.createElement("div");
      cn.className = "reply-cn";
      cn.textContent = it.cn;
      card.appendChild(cn);
    }

    card.addEventListener("click", () => {
      copyText(it.en || "");
      // 只保留最近一次点击的「已复制」
      box.querySelectorAll(".reply-card.copied").forEach((c) => c.classList.remove("copied"));
      card.classList.add("copied");
    });
    box.appendChild(card);
  }
}

/* ---------------- 提示条 ---------------- */
let toastTimer = null;

function showToast(text, ms) {
  // 外壳模式下提示条是独立窗口，弹窗内不再显示
  if (window.__TAURI_INTERNALS__) return;
  const el = $("toast");
  const t = $("toastText");
  if (!text) {
    el.hidden = true;
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
    return;
  }
  el.hidden = false;
  if (t) t.textContent = text; else el.textContent = text;
  if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
  if (ms > 0) {
    toastTimer = setTimeout(() => { el.hidden = true; toastTimer = null; }, ms);
  }
}

/* ---------------- 「我想说」→ WebSocket ---------------- */
let ws = null;
let seq = 0;

function setBusy(busy) {
  const btn = $("btnGenerate");
  btn.disabled = busy;
  btn.textContent = busy ? "生成中…" : "生成";
}

$("btnGenerate").addEventListener("click", () => {
  const intent = $("intent").value.trim();
  if (!intent) return;

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    renderReplies([], "");
    // 用自绘提示条，不用 window.alert（原生弹窗跟整套 UI 两个画风）
    invoke("show_notice", { text: "后端没连上，稍等重试", ms: 2600 });
    return;
  }
  const id = ++seq;
  setBusy(true);
  ws.send(JSON.stringify({
    type: "compose", id, intent,
    raw: current.raw, translation: current.translation,
  }));
});

function syncIntentHint() {
  const hint = document.getElementById("intentHint");
  const box = document.getElementById("intent");
  if (hint && box) hint.hidden = box.value.length > 0;
}
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("intent");
  if (box) {
    box.addEventListener("input", syncIntentHint);
    box.addEventListener("blur", syncIntentHint);
  }
});

$("intent").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btnGenerate").click();
});

/* ---------------- 应用设置（字体 / 缩放） ---------------- */
function applySettings(cfg) {
  if (!cfg) return;
  const sc = Number(cfg.ui_scale) || 1;
  window.__wowScale = sc;
  document.documentElement.style.zoom = sc;
  if (cfg.ui_font) {
    document.documentElement.style.setProperty(
      "--font", `"${cfg.ui_font}", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif`);
  }
}

/** 当前界面缩放比（1.0 = 100%） */
function SCALE() {
  const v = Number(window.__wowScale || 1);
  return Math.min(1.5, Math.max(0.8, v || 1));
}

/* ---------------- Tauri 桥（浏览器里跑时全部 no-op） ---------------- */

function syncWindowHeight() {
  const el = $("popup");
  if (!el) return;
  // 用 offsetHeight：它是布局高度，不会被 html 的 zoom 再乘一次
  const cssH = Math.ceil(el.offsetHeight || el.scrollHeight);
  // 别超过屏幕可用高的 94%，超了就靠页面内滚动（规格 §8）
  // 只能用屏幕高度当上限：用 innerHeight 会形成「窗口变矮→上限变小→继续变矮」的死循环
  const maxH = Math.floor((window.screen.availHeight || 800) * SCREEN_USE_RATIO);
  const h = Math.min(cssH, maxH);
  el.style.maxHeight = maxH + "px";
  if (h > 120) {
    invoke("fit_window", {
      width: 480 * SCALE(),
      height: h * SCALE(),
      dpr: window.devicePixelRatio || 1,
    });
  }
}

function showPopupWindow() {
  // 重新出现时清掉上次残留的星点动画帧
  if (window.__skyReset) window.__skyReset();
  invoke("show_popup");
  // 内容/字体/图片都可能让高度变化，多量几次
  syncWindowHeight();
  requestAnimationFrame(syncWindowHeight);
  setTimeout(syncWindowHeight, 80);
  setTimeout(syncWindowHeight, 260);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(syncWindowHeight);
  }
}

/* ---------------- 连接 ---------------- */
function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log("WS 已连接");
    ws.send(JSON.stringify({ type: "settings_get" }));
  };
  ws.onclose = () => {
    console.warn("WS 断开，2 秒后重连");
    setTimeout(connect, 2000);
  };
  ws.onerror = () => { /* onclose 会跟着触发 */ };

  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (err) { return; }
    if (msg.type === "show") {
      render(msg.payload);
      showToast("", 0);
      showPopupWindow();
    } else if (msg.type === "settings") {
      applySettings(msg.cfg);
      // 保存设置后立刻把顶部引擎标签切过来（不用等下一次翻译）
      const c = msg.cfg || {};
      const hasKey = !!(c.api && c.api.key_set);
      const eng = c.engine === "ai" ? (hasKey ? "ai" : "offline")
                : c.engine === "offline" ? "offline"
                : (hasKey ? "ai" : "offline");
      showEngine(eng);
      syncWindowHeight();
    } else if (msg.type === "notice") {
      showToast(msg.text, msg.ms || 0);
    } else if (msg.type === "compose") {
      setBusy(false);
      if (msg.ok) {
        renderReplies(msg.replies, "按你的意思 · 点一下复制");
      } else {
        renderReplies([], "");
        invoke("show_notice", {
          text: "生成失败：" + (msg.error || "未知错误"), ms: 4000,
        });
      }
    }
  };
}

/* ---------------- 启动 ---------------- */
if (location.protocol === "http:" || location.protocol === "https:") {
  render(MOCK);
  connect();
} else {
  render(MOCK);           // 直接双击文件：只看样式
}

$("btnClose").addEventListener("click", () => {
  if (window.__TAURI_INTERNALS__) {
    window.__TAURI_INTERNALS__.invoke("hide_popup");
  } else {
    document.body.style.opacity = "0.25";
  }
});

function syncBall() {
  const ball = $("drawerBall");
  if (ball) ball.hidden = false;   // 圆球常显，避免和点击时序互相打架
}

function toggleDrawer() {
  const f = $("floatReplies");
  if (!f) return;
  const open = !f.hidden && !f.classList.contains("closed");
  if (open) {
    f.classList.add("closed");
  } else {
    f.hidden = false;
    f.classList.remove("closed");
  }
  syncBall();
}

$("replyHead").addEventListener("click", toggleDrawer);
$("drawerBall").addEventListener("click", toggleDrawer);

// 点弹窗里除浮层/圆球以外的地方 → 自动收起
document.addEventListener("mousedown", (e) => {
  const f = $("floatReplies");
  if (!f || f.hidden || f.classList.contains("closed")) return;
  if (e.target.closest("#floatReplies") || e.target.closest("#drawerBall")) return;
  f.classList.add("closed");
  syncBall();
});

$("btnSettings").addEventListener("click", () => {
  if (window.__TAURI_INTERNALS__) {
    window.__TAURI_INTERNALS__.invoke("open_settings");
  } else {
    window.open("/settings.html", "_blank", "width=560,height=460");
  }
});

/* ---------------- 拖动窗口（只有顶栏） ---------------- */
document.querySelectorAll(".bar").forEach((bar) => {
  bar.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest("button")) return;   // 齿轮 / 关闭 不拖
    invoke("start_drag");
  });
});
