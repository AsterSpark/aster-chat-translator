/* 星标 SVG 定义（原来 4 个页面各存一份，改一处要改四处）

   用 currentScript 就地插入：脚本在 HTML 里放哪，图形就渲染到哪，
   不依赖 DOMContentLoaded，也就没有 <use> 先于定义解析的时序问题。 */
(function () {
  const markup = `<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <linearGradient id="gLight" x1="0" y1="0" x2=".8" y2="1">
      <stop offset="0" stop-color="#fffdf6"/><stop offset=".55" stop-color="#ffdda0"/><stop offset="1" stop-color="#e8ac4a"/>
    </linearGradient>
    <linearGradient id="gDark" x1="0" y1="0" x2=".8" y2="1">
      <stop offset="0" stop-color="#dfa23c"/><stop offset="1" stop-color="#8f5b16"/>
    </linearGradient>
  </defs>

  <!-- 主标：凹边四角星 · 切面 + 晶体核 -->
  <symbol id="star" viewBox="0 0 24 24">
    <path fill="url(#gLight)" d="M12 0 Q10.1 10.1 0 12 Q10.1 13.9 12 24 Z"/>
    <path fill="url(#gDark)"  d="M12 0 Q13.9 10.1 24 12 Q13.9 13.9 12 24 Z"/>
    <path fill="none" stroke="rgba(255,246,225,.34)" stroke-width=".4"
          d="M12 0 Q13.9 10.1 24 12 Q13.9 13.9 12 24 Q10.1 13.9 0 12 Q10.1 10.1 12 0 Z"/>
    <path fill="#fffdf6" opacity=".5"
          d="M12 5.4 Q12.75 11.25 18.6 12 Q12.75 12.75 12 18.6 Q11.25 12.75 5.4 12 Q11.25 11.25 12 5.4 Z"/>
    <path fill="#ffffff" opacity=".92"
          d="M12 9.2 Q12.35 11.65 14.8 12 Q12.35 12.35 12 14.8 Q11.65 12.35 9.2 12 Q11.65 11.65 12 9.2 Z"/>
  </symbol>

  <!-- 右下按钮：双星（都做切面 + 棱线 + 晶核，无星环） -->
  <symbol id="starFab" viewBox="0 0 24 24">
    <g transform="translate(11.4 12.6) scale(.74) translate(-12 -12)">
      <path fill="url(#gLight)" d="M12 0 Q10.1 10.1 0 12 Q10.1 13.9 12 24 Z"/>
      <path fill="url(#gDark)"  d="M12 0 Q13.9 10.1 24 12 Q13.9 13.9 12 24 Z"/>
      <path fill="none" stroke="rgba(255,246,225,.42)" stroke-width=".5"
            d="M12 0 Q13.9 10.1 24 12 Q13.9 13.9 12 24 Q10.1 13.9 0 12 Q10.1 10.1 12 0 Z"/>
      <path fill="#fffdf6" opacity=".55"
            d="M12 5.4 Q12.75 11.25 18.6 12 Q12.75 12.75 12 18.6 Q11.25 12.75 5.4 12 Q11.25 11.25 12 5.4 Z"/>
      <path fill="#ffffff" opacity=".95"
            d="M12 8.8 Q12.4 11.6 15.2 12 Q12.4 12.4 12 15.2 Q11.6 12.4 8.8 12 Q11.6 11.6 12 8.8 Z"/>
    </g>
    <g transform="translate(17.4 4.2) scale(.24)">
      <path fill="url(#gLight)" d="M12 0 Q10.1 10.1 0 12 Q10.1 13.9 12 24 Z"/>
      <path fill="url(#gDark)"  d="M12 0 Q13.9 10.1 24 12 Q13.9 13.9 12 24 Z"/>
      <path fill="none" stroke="rgba(255,246,225,.45)" stroke-width=".8"
            d="M12 0 Q13.9 10.1 24 12 Q13.9 13.9 12 24 Q10.1 13.9 0 12 Q10.1 10.1 12 0 Z"/>
      <path fill="#fffdf6" opacity=".6"
            d="M12 6.2 Q12.7 11.3 17.8 12 Q12.7 12.7 12 17.8 Q11.3 12.7 6.2 12 Q11.3 11.3 12 6.2 Z"/>
      <path fill="#ffffff" opacity=".95"
            d="M12 9.4 Q12.32 11.68 14.6 12 Q12.32 12.32 12 14.6 Q11.68 12.32 9.4 12 Q11.68 11.68 12 9.4 Z"/>
    </g>
  </symbol>
</svg>`;
  const here = document.currentScript;
  if (here && here.parentNode) here.insertAdjacentHTML("beforebegin", markup);
})();
