/* 星点动画：各窗口共用（坠星 + 流星，随机方向与节奏） */
/* ---------------- 星点：坠星与流星随机生成 ---------------- */
(function () {
  const sky = document.getElementById("sky");
  if (!sky) return;
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const rand = (a, b) => a + Math.random() * (b - a);
  const alive = new Map();          // el -> 截止时间

  function spawn(cls, setup) {
    const el = document.createElement(cls);
    const dur = setup(el);
    alive.set(el, Date.now() + dur * 1000 + 700);
    const drop = () => { el.remove(); alive.delete(el); };
    el.addEventListener("animationend", drop);
    el.addEventListener("animationcancel", drop);
    sky.appendChild(el);
  }

  // 兜底清扫：动画被挂起 / 掉帧 / 窗口隐藏导致 end 不触发时，按截止时间清掉
  setInterval(() => {
    const now = Date.now();
    for (const [el, deadline] of alive) {
      if (now > deadline) { el.remove(); alive.delete(el); }
    }
  }, 1200);

  // 窗口重新显示时，把在飞的清空，避免留下"卡住"的一帧
  window.__skyReset = () => {
    for (const el of alive.keys()) el.remove();
    alive.clear();
  };
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) window.__skyReset();
  });

  // 坠落星：位置、时长都随机，落到标题带底部淡出
  const spawnFall = () => spawn("u", (el) => {
    const dur = rand(7, 13);
    el.style.left = rand(4, 64).toFixed(1) + "%";
    el.style.animationDuration = dur.toFixed(1) + "s";
    return dur;
  });

  // 流星：顶部一闪而过的光束，左右两个方向各半
  const spawnGlint = () => spawn("s", (el) => {
    const dur = rand(0.95, 1.5);
    const leftward = Math.random() < 0.5;
    el.classList.toggle("rev", leftward);
    el.style.animationName = leftward ? "glintL" : "glintR";
    el.style.left = (leftward ? rand(30, 72) : rand(8, 52)).toFixed(1) + "%";
    el.style.top = rand(1, 9).toFixed(0) + "px";
    el.style.animationDuration = dur.toFixed(2) + "s";
    return dur;
  });

  // 频率：坠星 3.5~8 秒一颗，流星 9~20 秒一次
  const loopFall = () => { spawnFall(); setTimeout(loopFall, rand(3500, 8000)); };
  const loopGlint = () => { spawnGlint(); setTimeout(loopGlint, rand(9000, 20000)); };
  setTimeout(loopFall, 800);
  setTimeout(loopGlint, 1600);
})();
