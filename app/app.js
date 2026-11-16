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


/* 一行译文拆成 元信息 + 正文。
   排版定稿（v0.1.2）：元信息一行（时间 + 频道标签 + 说话人），正文一行。
   以前的两个坑：
     1) 名字长度卡 18 字，"Lavabeard Moltenbrow" 这种超了就落进正文，
        于是有的行两行、有的行一行，看着像排版坏了；
     2) 名字带方括号进正文，和游戏里一模一样但抢视觉。
   现在统一：`19:49 [1] Lavabeard Moltenbrow` / 正文（说话人去掉方括号）。
*/
const RX_TIME = /^(\d{1,2}:\d{2})\s*/;
const RX_TAGS = /^((?:\[[^\]]{1,40}\]\s*)+)[:：]?\s*/;
const RX_NAME = /^([^\[\]:：]{1,40})[:：]\s*/;

/* 当前显示的内容，供「我想说」当语境 */
let current = { raw: "", translation: "" };

function splitLine(line) {
  let s = (line || "").trim();
  let time = "";
  let tags = "";

  const mt = RX_TIME.exec(s);
  if (mt) { time = mt[1]; s = s.slice(mt[0].length); }

  const mg = RX_TAGS.exec(s);
  if (mg) {
    tags = mg[1].trim();
    s = s.slice(mg[0].length);
  }
  // 说话人：方括号后面紧跟 "Name: 正文" 时也要拆出来。
  // 判断依据是"最后一个方括号是频道/等级"（[1] / [1. 综合]），而不是"有没有括号"——
  // 以前只要有一个括号就跳过，于是 `09:54 [1] Name: 正文` 的人名被塞进了正文。
  const lastTag = (tags.match(/\[([^\]]*)\]\s*$/) || [])[1] || "";
  const channelish = !tags || /^[0-9]/.test(lastTag)
    || /综合|交易|本地防务|队伍|团队|公会|小队/.test(lastTag);
  if (channelish) {
    const mn = RX_NAME.exec(s);
    if (mn && !/[。！？!?]/.test(mn[1])) {
      tags = tags ? `${tags} ${mn[1].trim()}` : mn[1].trim();
      s = s.slice(mn[0].length);
    }
  }

  // 中文客户端：`[张三] 悄悄地对你说：` / `张三说：` —— 把"说"这部分也算进 meta
  if (!/说\s*[:：]/.test(tags)) {
    const ms = /^(.{1,20}?)\s*(悄悄地对你说|说)\s*[:：]\s*/.exec(s);
    if (ms && !/[。！？!?]/.test(ms[1])) {
      tags = tags ? `${tags} ${ms[1].trim()}${ms[2]}：` : `${ms[1].trim()}${ms[2]}：`;
      s = s.slice(ms[0].length);
    }
  }

  // 标签里最后一组是说话人：去掉方括号；前面的频道标签保留原样（[1] / [公会]）
  if (tags) {
    const parts = [...tags.matchAll(/\[([^\]]*)\]/g)].map((m) => m[1].trim());
    if (parts.length >= 2) {
      const name = parts.pop();
      tags = parts.map((p) => `[${p}]`).join(" ") + " " + name;
    }
  }

  const meta = [time, tags].filter(Boolean).join(" ");
  return { meta, body: s || (meta ? "" : line) };
}

/* 顶部那枚小标签：AI 模式显示「AI 模型」，本地词库模式显示「本地词库」 */
function showEngine(mode) {
  const el = $("engineNote");
  if (!el) return;
  el.hidden = false;
  const local = mode === "local";
  el.textContent = local ? "本地词库" : "AI 模型";
  el.dataset.mode = local ? "local" : "ai";
}

/* 「我想说」要调模型：本地词库模式下用不了，直接灰掉并说清楚原因，
   别让用户点了之后再收到一句"要填 Key" */
let composeOn = true;
let engineMode = "";      // 来自 settings 的引擎："" = AI，local = 本地词库
let busyNow = false;      // "生成中"，别被 setComposeEnabled 顺手解禁

function setComposeEnabled(on) {
  composeOn = !!on;
  const btn = $("btnGenerate");
  const box = $("intent");
  if (btn) btn.disabled = !composeOn || busyNow;
  if (box) box.disabled = !composeOn;
  // 输入框上方那行小字是自绘的（不是 placeholder），要改它而不是 placeholder
  const field = document.getElementById("intentHint");
  if (field) {
    field.textContent = composeOn ? "用中文写，回车生成英文" : "本地词库模式 · 切回 AI 模型才能生成";
    field.hidden = composeOn ? (box && box.value.length > 0) : false;
  }
  // 本地模式下抽屉里那行提示会和输入框挤在一起，藏掉
  const hint = $("drawerHint");
  if (hint) hint.hidden = !composeOn;
}

let rawLinesCache = [];
let rawResetTimer = null;
let rawTarget = null;          // null = 停在最新；数字 = 当前指向的那条
const RAW_ROW_H = 23;          // 单行高度，也是"把目标行顶到中间"需要的偏移

/* 建出全部原文行（视口只露 3 行，其余靠滚动） */
function buildRaw(lines) {
  const box = $("rawText");
  if (!box) return;
  rawLinesCache = lines;
  box.innerHTML = "";
  (lines.length ? lines : ["—"]).forEach((t, i) => {
    const d = document.createElement("div");
    d.className = "raw-line";
    d.dataset.i = i;
    d.title = t || "";
    d.textContent = t || "\u00a0";
    box.appendChild(d);
  });
}

/* 指向第 i 条：高亮它，并平滑滚到让它停在中间那格（首尾由上下留白补足） */
function focusRaw(i) {
  const box = $("rawText");
  if (!box) return;
  const line = box.querySelector(`.raw-line[data-i="${i}"]`);
  box.querySelectorAll(".raw-line.hl").forEach((el) => el.classList.remove("hl"));
  box.classList.toggle("hl-on", !!line);
  if (!line) return;
  line.classList.add("hl");
  rawTarget = i;
  const target = Math.max(0, line.offsetTop - box.offsetTop - RAW_ROW_H);
  box.scrollTo({ top: target, behavior: window.__rawInstant ? "auto" : "smooth" });
}

/* 鼠标移开：去掉高亮，平滑滚回最新（最后一条停在中间） */
function resetRaw(instant) {
  const box = $("rawText");
  if (!box) return;
  box.classList.remove("hl-on");
  box.querySelectorAll(".raw-line.hl").forEach((el) => el.classList.remove("hl"));
  rawTarget = null;
  const top = box.scrollHeight - box.clientHeight;
  if (instant || window.__rawInstant) box.scrollTop = top;
  else box.scrollTo({ top, behavior: "smooth" });
}

function scheduleRawReset() {
  if (rawResetTimer) clearTimeout(rawResetTimer);
  rawResetTimer = setTimeout(() => {
    rawResetTimer = null;
    resetRaw(false);
  }, 120);
}

function render(result) {
  current.raw = result.raw || "";
  current.translation = result.translation || "";
  current.mode = result.mode || "";      // "" = AI，local = 本地词库
  showEngine(current.mode);
  setComposeEnabled(engineMode !== "local");

  const judgeCard = $("judgeCard");
  const transCard = $("transRows") && $("transRows").closest(".card");
  if (transCard) transCard.hidden = false;   // 切换态可能把它藏过

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
  // 原文：建出全部行，视口三行，默认滚到最新一条（它停在中间）
  let rawArr = (result.raw || "—").split("\n");
  while (rawArr.length > 1 && !rawArr[rawArr.length - 1].trim()) rawArr.pop();
  buildRaw(rawArr);
  resetRaw(true);
  // 字体/尺寸变化后再校一次：如果这时用户已经指向某条，就保持在那条上，别弹回最新
  requestAnimationFrame(() => {
    if (rawTarget === null) resetRaw(true);
    else focusRaw(rawTarget);
  });

  const rows = $("transRows");
  rows.innerHTML = "";
  rows.onmouseleave = scheduleRawReset;
  const allLines = (result.translation || "—").split("\n").filter((l) => l.trim());
  const MAX_VISIBLE = 6;          // 译文区最多露出 6 条，再多就滚动
  const lines = allLines;

  // 原文卡片只放得下三行，译文最多露六条 —— 超出的部分光看卡片不知道还有，
  // 标题里报总数，至少让人知道能滚。
  const nRaw = (result.raw || "").split("\n").filter((l) => l.trim()).length;
  const rawTitle = $("rawTitle");
  if (rawTitle) rawTitle.textContent = nRaw > 3 ? `原文 · ${nRaw} 条` : "原文";
  const transTitle = $("transTitle");
  if (transTitle) {
    transTitle.textContent = allLines.length > MAX_VISIBLE
      ? `翻译 · ${allLines.length} 条` : "翻译";
  }
  const rowStarts = [];   // 每条聊天对应的第一个 DOM 行（一条可能占 meta+正文两行）
  lines.forEach((line, k) => {
    const { meta, body } = splitLine(line);
    const row = document.createElement("div");
    row.className = "row";
    row.addEventListener("mouseenter", () => {
      if (rawResetTimer) { clearTimeout(rawResetTimer); rawResetTimer = null; }
      row.classList.add("hl");
      focusRaw(k);                 // 对应原文平滑滚到中间
    });
    row.addEventListener("mouseleave", () => {
      row.classList.remove("hl");
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
    rowStarts.push(row);
  });

  // 超过 6 条：裁到刚好露出最后 6 条（按"聊天条数"算，一条占 1~2 个 DOM 行），
  // 默认滚到最底下，也就是最新的一条。只做上下滚动。
  (function capTransRows() {
    rows.style.maxHeight = "none";
    rows.style.overflowY = "hidden";
    rows.style.overflowX = "hidden";   // 别让 -8px 的负边距撑出横向滚动条
    rows.style.paddingRight = "0";
    if (rowStarts.length > MAX_VISIBLE) {
      // 必须用 offsetTop/offsetHeight：getBoundingClientRect 会被 html zoom 放大，
      // 而 maxHeight 要的是未缩放布局值（120% 时会多露一条、80% 时少露一条）。
      const start = rowStarts[rowStarts.length - MAX_VISIBLE];
      const bottom = rowStarts[rowStarts.length - 1].offsetTop
                   + rowStarts[rowStarts.length - 1].offsetHeight;
      const h = bottom - start.offsetTop + 2;
      rows.style.maxHeight = Math.max(48, h) + "px";
      rows.style.overflowY = "auto";
      rows.style.paddingRight = "6px";   // 给滚动条让位，别压字
      rows.scrollTop = rows.scrollHeight;
    } else {
      rows.scrollTop = 0;
    }
  })();

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
  if (hint) hint.hidden = !composeOn;
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
    $("replyHead").textContent = "建议回复 · 点一下复制";
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
    // 键盘也能用：Tab 聚焦、Enter/Space 复制
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", "复制这条英文回复");

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

    const doCopy = () => {
      copyText(it.en || "");
      // 只保留最近一次点击的「已复制」
      box.querySelectorAll(".reply-card.copied").forEach((c) => c.classList.remove("copied"));
      card.classList.add("copied");
    };
    card.addEventListener("click", doCopy);
    card.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      // 空格不要滚动页面
      e.preventDefault();
      doCopy();
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
  busyNow = !!busy;
  const btn = $("btnGenerate");
  btn.disabled = busyNow || !composeOn;   // 本地词库模式下永远保持禁用
  btn.textContent = busyNow ? "生成中…" : "生成";
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
  try {
    ws.send(JSON.stringify({
      type: "compose", id, intent,
      raw: current.raw, translation: current.translation,
    }));
  } catch (e) {
    setBusy(false);
    invoke("show_notice", { text: "发送失败，稍后重试", ms: 2600 });
    return;
  }
  // 兜底：后端重启/丢包时不会有 compose 回执，30 秒后自己解开按钮
  setTimeout(() => { if ($("btnGenerate").disabled) setBusy(false); }, 30000);
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
  if (e.key !== "Enter") return;
  if (e.isComposing || e.keyCode === 229) return;   // 输入法选词的回车不算
  e.preventDefault();
  $("btnGenerate").click();
});

/* ---------------- 应用设置（字体 / 缩放） ---------------- */
function applySettings(cfg) {
  if (!cfg) return;
  const sc = Number(cfg.ui_scale) || 1;
  window.__wowScale = sc;
  document.documentElement.style.zoom = sc;
  applyFont(cfg.ui_font);
}

/* ui_font 为空 = 用系统默认字体：必须把内联变量删掉，不然窗口 hide() 后再打开
   还挂着上一次设的字体（窗口是隐藏不是销毁，样式会留着）。 */
function applyFont(font) {
  if (font) {
    document.documentElement.style.setProperty(
      "--font", `"${font}", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif`);
  } else {
    document.documentElement.style.removeProperty("--font");
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
  // 上限按"缩放后不超过屏幕"算：fit_window 会把高度再乘 SCALE()
  const maxH = Math.floor((window.screen.availHeight || 800) * SCREEN_USE_RATIO / SCALE());
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

/* 切换模式时立即把弹窗切成新模式：
   旧结果是另一种模式翻的，继续挂着会误导（标签是本地词库、内容却是 AI 译文）。
   这里只换显示状态 + 清掉旧内容；真正的翻译等用户按热键 —— 设置窗开着时截图
   会把设置窗自己也截进去，所以不在切换瞬间自动翻。 */
/* 还没翻译时的"待机"画面：标题 + 原文区提示都跟着模式变，两种模式一眼能分清 */
function showIdleView(mode) {
  const local = mode === "local";
  $("barTitle").textContent = local ? "已切换到「本地词库」" : "已切换到「AI 模型」";
  if ($("rawTitle")) $("rawTitle").textContent = "原文";
  if ($("transTitle")) $("transTitle").textContent = "翻译";
  $("rawText").textContent = local
    ? "本地词库模式：按热键后用词库逐词对照\n只会得到关键词大意，不是完整翻译"
    : "AI 模式：按热键后把截图发给模型\n会得到完整译文";
  $("transRows").innerHTML = "";
  // 空着一张"翻译"卡片看着像坏了：待机态先藏起来，等真的出结果再显示
  const tc = $("transRows") && $("transRows").closest(".card");
  if (tc) tc.hidden = true;
  const jc = $("judgeCard");
  if (jc) jc.hidden = true;
  clearReplies();
  syncWindowHeight();
}

function applyEngineSwitch(mode) {
  const local = mode === "local";
  const next = local ? "local" : "";
  if (current.mode === next) return;
  current.mode = next;
  current.raw = "";
  current.translation = "";
  showEngine(current.mode);
  setComposeEnabled(!local);
  showIdleView(current.mode);
  // 这里不能 show_popup：设置窗这时在前台，弹窗一出来就把设置挡住了
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
    setBusy(false);                       // 断线时别把「生成」按钮永远卡在"生成中…"
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
      // 模式变了要立刻反映到弹窗上（不用等下一次翻译）。
      // 注意：还没有任何结果时（比如刚启动）只更新标签，不要弹切换提示窗。
      const eng = (msg.cfg || {}).engine === "local" ? "local" : "";
      engineMode = eng;
      setComposeEnabled(eng !== "local");
      if (current.mode !== eng) {
        if (current.raw || current.translation) {
          applyEngineSwitch(eng);
        } else {
          current.mode = eng;
          showEngine(current.mode);
          setComposeEnabled(eng !== "local");
          showIdleView(current.mode);   // 待机画面也要跟着模式变
        }
      } else {
        showEngine(current.mode);
      }
      syncWindowHeight();
    } else if (msg.type === "notice") {
      showToast(msg.text, msg.ms || 0);
    } else if (msg.type === "need_capture") {
      // 还没框选聊天区域：直接把设置窗拉起来（设置页会标红「重新框选」）
      if (window.__TAURI_INTERNALS__) {
        window.__TAURI_INTERNALS__.invoke("open_settings");
      }
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
if (window.__TAURI_INTERNALS__) {
  connect();              // 外壳里：等后端的真数据，别先渲染一份假聊天记录
} else if (location.protocol === "http:" || location.protocol === "https:") {
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

/* Esc = 关掉当前这个弹窗（不用去够右上角的 ✕）。
   输入法正在组词时 Esc 是给输入法的，不抢；正在生成回复也照样允许关。 */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (e.isComposing || e.keyCode === 229) return;
  e.preventDefault();
  if (window.__TAURI_INTERNALS__) {
    window.__TAURI_INTERNALS__.invoke("hide_popup");
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
