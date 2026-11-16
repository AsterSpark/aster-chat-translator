/* 截图区域框选：拖一个框，回车保存到配置（物理像素） */

const sel = document.getElementById("sel");
const sizeTag = document.getElementById("size");

let start = null;
let rect = null;
let ws = null;


function hint(text) {
  const el = document.getElementById("hint");
  if (el) el.textContent = text;
}

function connect() {
  try {
    ws = new WebSocket(WS_URL);
    // 后端回执要接住：原来没有 onmessage，框选失败（比如连不上后端）时
    // 窗口照常关闭、区域其实没存，用户完全不知道。
    ws.onmessage = (e) => {
      let m;
      try { m = JSON.parse(e.data); } catch (err) { return; }
      if (m.type !== "capture_saved") return;
      if (m.ok) {
        invoke("close_selector");
      } else {
        hint("保存失败：" + (m.error || "未知原因") + "，重新拖一个框再试");
      }
    };
    ws.onclose = () => setTimeout(connect, 2000);
  } catch (e) {}
}
connect();

function norm(a, b) {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    w: Math.abs(a.x - b.x),
    h: Math.abs(a.y - b.y),
  };
}

function paint() {
  if (!rect || rect.w < 2 || rect.h < 2) {
    sel.hidden = true;
    sizeTag.hidden = true;
    return;
  }
  sel.hidden = false;
  sel.style.left = rect.x + "px";
  sel.style.top = rect.y + "px";
  sel.style.width = rect.w + "px";
  sel.style.height = rect.h + "px";

  const dpr = window.devicePixelRatio || 1;
  sizeTag.hidden = false;
  sizeTag.textContent = `${Math.round(rect.w * dpr)} × ${Math.round(rect.h * dpr)}`;
  sizeTag.style.left = rect.x + "px";
  sizeTag.style.top = Math.max(4, rect.y - 26) + "px";
}

document.addEventListener("mousedown", (e) => {
  start = { x: e.clientX, y: e.clientY };
  rect = { x: start.x, y: start.y, w: 0, h: 0 };
  paint();
});

document.addEventListener("mousemove", (e) => {
  if (!start) return;
  rect = norm(start, { x: e.clientX, y: e.clientY });
  paint();
});

document.addEventListener("mouseup", () => {
  start = null;
  paint();
});

document.addEventListener("keydown", async (e) => {
  if (e.key === "Escape") {
    invoke("close_selector");
    return;
  }
  if (e.key !== "Enter") return;
  if (!rect || rect.w < 8 || rect.h < 8) {
    document.getElementById("hint").textContent = "框太小了，重新拖一个";
    return;
  }
  const dpr = window.devicePixelRatio || 1;
  // 窗口可能铺在多个显示器上（原点未必是 0,0，副屏在主屏左边时是负数）。
  // 窗口物理原点由 Rust 侧写进 window.__origin，这里加上才能得到绝对屏幕坐标。
  const origin = Array.isArray(window.__origin) ? window.__origin : [0, 0];
  const payload = {
    left: origin[0] + Math.round(rect.x * dpr),
    top: origin[1] + Math.round(rect.y * dpr),
    right: origin[0] + Math.round((rect.x + rect.w) * dpr),
    bottom: origin[1] + Math.round((rect.y + rect.h) * dpr),
  };
  if (!(ws && ws.readyState === WebSocket.OPEN)) {
    // 不关窗：关了就永远看不到"为什么没存上"
    hint("后端没连上，区域没有保存（先启动助手再框选）");
    return;
  }
  ws.send(JSON.stringify({ type: "set_capture", rect: payload }));
  hint("正在保存…");
  // 兜底：2 秒没回执就提示，窗口留着别关
  setTimeout(() => {
    if (!document.getElementById("sel").hidden) {
      hint("没收到后端确认，区域可能没保存，重试一次或看日志");
    }
  }, 2000);
});
