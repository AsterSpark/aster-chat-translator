// 禁止 release 版本弹控制台窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use tauri::Manager;
use tauri::WebviewWindow;

/// 后台 Python 后端的进程句柄，退出时杀掉
struct Sidecar(std::sync::Arc<Mutex<Option<std::process::Child>>>);

/// Job Object 句柄：外壳一死（哪怕是强杀/崩溃），整棵子进程树跟着死。
/// 这个句柄只要活着就行，代码里不需要读它 —— 所以字段标记 allow(dead_code)。
struct Job(#[allow(dead_code)] Mutex<Option<isize>>);

/// 页面最后一次报上来的 devicePixelRatio。
/// Tauri 的 scale_factor(1.5) 和 WebView2 的 dpr(1.8) 不是一回事，
/// 算提示条的位置必须用页面那份（尺寸也是按它算的），否则 150% 缩放下会偏。
struct LastDpr(Mutex<f64>);

/// 菜单刚弹出的时间：45ms 轮询别把"打开菜单的那一下"当成"点了外面"
struct MenuOpenedAt(Mutex<Option<std::time::Instant>>);

/// 本进程是不是第二个实例（用命名互斥量判断）。
///
/// 为什么不用插件：tauri_plugin_single_instance 的检查发生在 build() 里，
/// 而 sidecar 在那之前就已经拉起来了 —— 重复双击会白跑一个 Python 进程
/// （1~3 秒后才因为端口占用自己退出）。这里提前问一句，第二份就不拉后端了。
#[cfg(windows)]
fn already_running() -> bool {
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows_sys::Win32::System::Threading::CreateMutexW;

    let name: Vec<u16> = "Local\\WoWTranslator-SingleInstance\0"
        .encode_utf16()
        .collect();
    unsafe {
        // 句柄故意不关：进程活着就代表锁还在，进程没了系统自动回收
        let h = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        if h.is_null() {
            return false;   // 拿不到锁就别拦人，宁可多开也不要开不了
        }
        GetLastError() == ERROR_ALREADY_EXISTS
    }
}

#[cfg(not(windows))]
fn already_running() -> bool {
    false
}

/// 往 exe 同级的 runtime.log 追加一行（日志窗口读的就是这个文件）。
/// 外壳自己出问题时后端可能已经死了，日志窗口拉不到内容，所以这一步必须
/// 直接写盘，给重启后留证据。
fn log_line(msg: &str) {
    use std::io::Write;
    let dir = match std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
    {
        Some(d) => d,
        None => return,
    };
    // 带上 [HH:MM:SS]：日志窗口按这个格式解析，没时间戳的行时间列会是空的
    let stamp = local_hms();
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("runtime.log"))
    {
        let _ = writeln!(f, "[{stamp}] [壳] {msg}");
    }
}

/// 本地时间 HH:MM:SS（Win32 直接取，免得自己算时区/夏令时）
#[cfg(windows)]
fn local_hms() -> String {
    #[repr(C)]
    #[allow(dead_code)]      // 字段布局必须与 Win32 对齐，只有 hour/minute/second 会用到
    struct SystemTimeInfo {
        year: u16,
        month: u16,
        day_of_week: u16,
        day: u16,
        hour: u16,
        minute: u16,
        second: u16,
        milliseconds: u16,
    }
    extern "system" {
        fn GetLocalTime(st: *mut SystemTimeInfo);
    }
    let mut st = SystemTimeInfo {
        year: 0,
        month: 0,
        day_of_week: 0,
        day: 0,
        hour: 0,
        minute: 0,
        second: 0,
        milliseconds: 0,
    };
    unsafe { GetLocalTime(&mut st) };
    format!("{:02}:{:02}:{:02}", st.hour, st.minute, st.second)
}

#[cfg(not(windows))]
fn local_hms() -> String {
    String::from("--:--:--")
}

/// 弹一个系统消息框。release 版本没有控制台，eprintln 等于扔进黑洞，
/// 而 sidecar 死了 = 翻译功能整体失效，必须让用户看见。
#[cfg(windows)]
fn alert(title: &str, text: &str) {
    use std::os::windows::ffi::OsStrExt;
    fn wide(s: &str) -> Vec<u16> {
        std::ffi::OsStr::new(s)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }
    extern "system" {
        fn MessageBoxW(hwnd: isize, text: *const u16, caption: *const u16, utype: u32) -> i32;
    }
    const MB_ICONERROR: u32 = 0x10;
    const MB_TOPMOST: u32 = 0x40000;
    unsafe {
        MessageBoxW(
            0,
            wide(text).as_ptr(),
            wide(title).as_ptr(),
            MB_ICONERROR | MB_TOPMOST,
        );
    }
}

#[cfg(not(windows))]
fn alert(_title: &str, _text: &str) {}

#[cfg(windows)]
fn make_kill_on_close_job(child: &std::process::Child) -> Option<isize> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return None;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 {
            // 失败也要关句柄：不然每次启动都漏一个内核对象
            CloseHandle(job);
            return None;
        }
        let handle = child.as_raw_handle() as HANDLE;
        if AssignProcessToJobObject(job, handle) == 0 {
            CloseHandle(job);
            return None;
        }
        Some(job as isize)
    }
}

/// 启动同目录下的 core\AsterCore.exe
fn start_sidecar() -> Option<std::process::Child> {
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let core = dir.join("core").join("AsterCore.exe");
    if !core.exists() {
        eprintln!("没找到后端: {}", core.display());
        return None;
    }
    match std::process::Command::new(&core)
        .current_dir(&dir)
        .env("WOWTR_HOME", &dir)
        .env("WOWTR_PARENT_PID", std::process::id().to_string())
        .spawn()
    {
        Ok(child) => {
            eprintln!("后端已启动 pid={}", child.id());
            Some(child)
        }
        Err(e) => {
            eprintln!("后端启动失败: {e}");
            None
        }
    }
}


/// 显示弹窗（前端收到 WS 数据后调用）
/// 把弹窗摆回固定位置：水平居中、顶部留 12% 屏高。
/// 以前只在创建时 center 一次，之后每次内容变高，fit_window 的"超出屏幕往上推"
/// 会把它一点点顶走，而且再也回不来 —— 表现就是"每次打开位置都不一样"。
/// 弹窗的"惯用顶部"：屏高 12% 处（不是按窗口高度算，避免内容一变就上下跳）
#[cfg(windows)]
fn popup_anchor_y(mon_pos_y: i32, mon_h: i32, win_h: i32) -> i32 {
    let y = mon_pos_y + ((mon_h as f64) * 0.12).round() as i32;
    let max_y = mon_pos_y + mon_h - win_h;   // 别长出屏幕底边
    y.min(max_y).max(mon_pos_y)
}

#[cfg(windows)]
fn place_popup(window: &WebviewWindow) {
    if let Ok(Some(mon)) = window.current_monitor() {
        let mp = mon.position();
        let ms = mon.size();
        if let Ok(size) = window.outer_size() {
            let x = mp.x + (ms.width as i32 - size.width as i32) / 2;
            let y = popup_anchor_y(mp.y, ms.height as i32, size.height as i32);
            let _ = window.set_position(tauri::PhysicalPosition::new(x, y));
        }
    }
}

#[tauri::command]
fn show_popup(window: WebviewWindow) -> Result<(), String> {
    // 必须让弹窗拿到焦点，否则在"全屏游戏是前台"的情况下 Windows 会拒绝激活它：
    // 窗口浮在上面但点不动（拖不动、✕ 也没反应，实测）。
    // 任务栏因此被翻出来的问题交给"提示条不抢焦点"来缓解（见 place_and_show_toast）。
    #[cfg(windows)]
    place_popup(&window);
    window.show().map_err(|e| e.to_string())?;
    if let Err(e) = window.set_focus() {
        // 抢不到焦点时窗口会"浮在上面但点不动"，只丢给前端 console 是查不出来的
        log_line(&format!("show_popup: set_focus 失败（窗口可能点不动）：{e}"));
        return Err(e.to_string());
    }
    Ok(())
}

/// 把窗口提到"最上层窗口"的最前面。
/// 提示条和弹窗都是 alwaysOnTop，谁最后 SetWindowPos 谁在上面 —— 弹窗刚显示过时
/// 会把提示条压在下面，所以每次弹提示条都要再抢一次 z 序（不激活，不抢焦点）。
#[cfg(windows)]
fn raise_topmost(window: &WebviewWindow) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
    };
    if let Ok(hwnd) = window.hwnd() {
        unsafe {
            SetWindowPos(hwnd.0 as _, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
        }
    }
}

/// 显示窗口但不激活（不抢前台）
#[cfg(windows)]
fn show_no_activate(window: &WebviewWindow) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{ShowWindow, SW_SHOWNOACTIVATE};
    let hwnd = window.hwnd().map_err(|e| e.to_string())?;
    unsafe {
        ShowWindow(hwnd.0 as _, SW_SHOWNOACTIVATE);
    }
    Ok(())
}

#[cfg(not(windows))]
fn show_no_activate(window: &WebviewWindow) -> Result<(), String> {
    window.show().map_err(|e| e.to_string())
}

/// 拖动窗口（无边框窗口要靠页面通知才会动）
#[tauri::command]
fn start_drag(window: WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(|e| e.to_string())
}

/// 隐藏弹窗
#[tauri::command]
fn hide_popup(window: WebviewWindow) -> Result<(), String> {
    window.hide().map_err(|e| e.to_string())
}

/// 按页面自己的 CSS 尺寸来定窗口大小。
///
/// 为什么不让 Rust 自己算：DPI 在不同上下文里可能读到不一样的值
/// （Tauri 报 1.5、WebView2 的 devicePixelRatio 报 1.8 就见过），
/// 由页面把「我要多宽多高 + 我这里的 dpr」报上来，换算成物理像素最稳。
#[tauri::command]
fn fit_window(window: WebviewWindow, width: f64, height: f64, dpr: f64) -> Result<(), String> {
    let dpr = if dpr > 0.5 { dpr } else { 1.0 };
    // 记下页面报的 dpr：提示条定位要用同一套换算（Tauri 的 scale_factor 不一样）
    // 只记提示条那份：其它窗口的 dpr 会把提示条定位带偏（混合 DPI 时尤其明显）
    if window.label() == "toast" {
        if let Some(state) = window.app_handle().try_state::<LastDpr>() {
            if let Ok(mut g) = state.0.lock() {
                *g = dpr;
            }
        }
    }
    // 上限兜底：页面万一报个离谱值，别把窗口设成 10 万像素
    let w = ((width * dpr).round() as i64).clamp(200, 8000) as u32;
    let h = ((height * dpr).round() as i64).clamp(120, 8000) as u32;
    window
        .set_size(tauri::PhysicalSize::new(w, h))
        .map_err(|e| e.to_string())?;

    // 位置校正：内容变高时窗口会往下长，长出屏幕底边就看不见了。
    // 只改尺寸不改位置的话，超出部分在屏幕外（实测 1080p 下长译文会掉出屏幕）。
    if let Ok(pos) = window.outer_position() {
        if let Ok(Some(mon)) = window.current_monitor() {
            let mp = mon.position();
            let ms = mon.size();
            let (l, t) = (mp.x, mp.y);
            let (r, b) = (l + ms.width as i32, t + ms.height as i32);
            let mut x = pos.x;
            let mut y = pos.y;
            if window.label() == "popup" {
                // 弹窗：顶部锚在屏高 12%，换内容只影响高度，不再上下漂
                y = popup_anchor_y(t, ms.height as i32, h as i32);
            }
            if x + w as i32 > r { x = r - w as i32; }
            if y + h as i32 > b { y = b - h as i32; }
            if x < l { x = l; }
            if y < t { y = t; }
            if x != pos.x || y != pos.y {
                let _ = window.set_position(tauri::PhysicalPosition::new(x, y));
            }
        }
    }
    Ok(())
}

/// 打开设置窗口
#[tauri::command]
fn open_settings(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("settings") {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
        win.center().map_err(|e| e.to_string())?;
        // 让页面重新拉一次配置：上次可能留着没保存的改动，重开时要以已保存的为准
        let _ = win.eval("window.__refresh && window.__refresh();");
        trigger_post("/window_settings?open=1");
        return Ok(());
    }
    log_line("设置窗口不存在，open_settings 失败");
    Err("设置窗口不可用".into())
}

/// 顶部提示条：显示并挪到屏幕顶部中间
#[tauri::command]
fn show_toast(window: WebviewWindow) -> Result<(), String> {
    place_and_show_toast(&window)
}

fn place_and_show_toast(window: &WebviewWindow) -> Result<(), String> {
    // 用页面报的 dpr 而不是 Tauri 的 scale_factor：尺寸是按 dpr 算的，
    // 位置要是按 scale_factor 算，两者不一致时就会偏（1.5 vs 1.8 见过）。
    let dpr = window
        .app_handle()
        .try_state::<LastDpr>()
        .and_then(|s| s.0.lock().ok().map(|g| *g))
        .filter(|v| *v > 0.5)
        .unwrap_or_else(|| window.scale_factor().unwrap_or(1.0));
    let size = window.outer_size().map_err(|e| e.to_string())?;
    if let Some(mon) = window.current_monitor().map_err(|e| e.to_string())? {
        let mp = mon.position();
        let ms = mon.size();
        // 必须加上显示器原点：主屏不在 (0,0) 时（副屏在左/上、竖屏）否则会飘到别的屏
        let x = mp.x as f64 + (ms.width as f64 - size.width as f64) / 2.0;
        let y = mp.y as f64 + 44.0 * dpr;
        let _ = window.set_position(tauri::PhysicalPosition::new(x as i32, y as i32));
    }
    // 提示条只是显示，绝不能抢前台 —— 一抢焦点，全屏游戏的任务栏就会翻出来
    show_no_activate(window)?;
    // 再抢一次 z 序：弹窗也是 topmost，刚显示过它就会把提示条压住（用户实测遮挡）
    #[cfg(windows)]
    raise_topmost(window);
    Ok(())
}

/// 把 Rust 侧的一句话渲染到自绘提示条上（不弹系统对话框）。
///
/// 后端挂了的时候 WS 是死的，但提示条窗口本身还在，直接 eval 进页面即可 ——
/// 这样"后端退出""启动失败"之类的提示和正常提示长得完全一样。
fn toast_text(app: &tauri::AppHandle, text: &str, ms: u64) -> bool {
    fn quote(s: &str) -> String {
        let mut out = String::from("\"");
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                _ => out.push(c),
            }
        }
        out.push('"');
        out
    }
    let win = match app.get_webview_window("toast") {
        Some(w) => w,
        None => return false,
    };
    let js = format!(
        "window.__showNotice && window.__showNotice({}, {ms}, \"\")",
        quote(text)
    );
    // eval 失败 = 页面还没加载好（启动早期常见）。这时既显示不出提示，又会留下
    // 一个空的置顶提示条窗口挡鼠标，所以直接当失败处理。
    if win.eval(&js).is_err() {
        return false;
    }
    let _ = place_and_show_toast(&win);
    true
}

/// 前端也能主动弹一条自绘提示（替代 window.alert）
#[tauri::command]
fn show_notice(app: tauri::AppHandle, text: String, ms: Option<u64>) -> Result<(), String> {
    if toast_text(&app, &text, ms.unwrap_or(2400)) {
        Ok(())
    } else {
        Err("提示条窗口不可用".into())
    }
}

/// 退出整个程序（设置窗口里的「退出程序」）
#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0);
}

/// 关闭自己（设置窗口的 ✕ / 提示条自动收起）
#[tauri::command]
fn close_self(window: WebviewWindow) -> Result<(), String> {
    if window.label() == "settings" {
        trigger_post("/window_settings?open=0");
        return window.hide().map_err(|e| e.to_string());
    }
    // 日志窗只是 hide()，页面里的 1.2s 轮询不会自己停（Tauri 下 document.hidden 不变）
    if window.label() == "logs" {
        let _ = window.eval("window.__pause && window.__pause();");
    }
    window.hide().map_err(|e| e.to_string())
}

/// 开机自启（写 HKCU 的 Run 项，不需要管理员）
const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
const RUN_NAME: &str = "AsterChat";

/// 跑一条命令并拿走输出，**不弹控制台窗口**。
/// GUI 进程里直接 spawn 控制台程序（reg.exe）会闪一下黑框，很扎眼。
/// 系统目录下的绝对路径：直接写 "reg" / "explorer" 会按 PATH + 当前目录搜索，
/// 别人往程序目录丢个同名 exe 就能被顺带执行。
fn system_exe(name: &str) -> String {
    let root = std::env::var("SystemRoot").unwrap_or_else(|_| "C:\\Windows".to_string());
    format!("{root}\\System32\\{name}")
}

fn explorer_exe() -> String {
    let win = std::env::var("WINDIR").unwrap_or_else(|_| "C:\\Windows".to_string());
    format!("{win}\\explorer.exe")
}

#[cfg(windows)]
fn run_quiet(exe: &str, args: &[&str]) -> std::io::Result<std::process::Output> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    std::process::Command::new(exe)
        .args(args)
        .creation_flags(CREATE_NO_WINDOW)
        .output()
}

#[tauri::command]
fn get_autostart() -> bool {
    #[cfg(windows)]
    {
        return matches!(
            run_quiet(&system_exe("reg.exe"), &["query", RUN_KEY, "/v", RUN_NAME]),
            Ok(o) if o.status.success()
        );
    }
    #[cfg(not(windows))]
    false
}

#[tauri::command]
fn set_autostart(enabled: bool) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let quoted = format!("\"{}\"", exe.display());
    #[cfg(windows)]
    let out = if enabled {
        run_quiet(&system_exe("reg.exe"),
                  &["add", RUN_KEY, "/v", RUN_NAME, "/t", "REG_SZ", "/d", &quoted, "/f"])
    } else {
        run_quiet(&system_exe("reg.exe"), &["delete", RUN_KEY, "/v", RUN_NAME, "/f"])
    };
    #[cfg(not(windows))]
    let out: std::io::Result<std::process::Output> = Err(std::io::Error::other("仅 Windows"));

    match out {
        Ok(o) if o.status.success() => Ok(()),
        // reg.exe 的 stderr 是 GBK，from_utf8_lossy 只会得到乱码；给个能看懂的固定文案
        Ok(_) => Err("注册表写入失败（可能被安全软件拦了）".into()),
        Err(e) => Err(e.to_string()),
    }
}

/// 打开截图区域框选窗口（铺满屏幕，拖一个框）
#[tauri::command]
fn open_selector(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("selector") {
        // 覆盖所有显示器：tauri.conf 里的 fullscreen 只按主屏生效，
        // 游戏在副屏时框不到（实测配置成 fullscreen 时窗口就锁在主屏）。
        let mut origin = (0i32, 0i32);
        if let Ok(mons) = win.available_monitors() {
            if !mons.is_empty() {
                let (mut l, mut t, mut r, mut b) = (i32::MAX, i32::MAX, i32::MIN, i32::MIN);
                for m in &mons {
                    let p = m.position();
                    let s = m.size();
                    l = l.min(p.x);
                    t = t.min(p.y);
                    r = r.max(p.x + s.width as i32);
                    b = b.max(p.y + s.height as i32);
                }
                origin = (l, t);
                let _ = win.set_position(tauri::PhysicalPosition::new(l, t));
                let _ = win.set_size(tauri::PhysicalSize::new((r - l) as u32, (b - t) as u32));
            }
        }
        // 页面按视口坐标算框选位置，副屏在左边时视口原点不等于屏幕原点，
        // 把窗口的物理原点告诉它，保存时才能换算成绝对屏幕坐标。
        let _ = win.eval(&format!("window.__origin = [{}, {}];", origin.0, origin.1));
        // 上次框选残留的选框/提示要清掉（窗口只是 hide，页面不重建）
        let _ = win.eval("window.__reset && window.__reset();");
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn close_selector(window: WebviewWindow) -> Result<(), String> {
    window.hide().map_err(|e| e.to_string())
}

/// 光标位置（物理像素）
#[cfg(windows)]
fn cursor_pos() -> Option<(i32, i32)> {
    #[repr(C)]
    struct Point { x: i32, y: i32 }
    extern "system" {
        fn GetCursorPos(p: *mut Point) -> i32;
    }
    let mut p = Point { x: 0, y: 0 };
    let ok = unsafe { GetCursorPos(&mut p) };
    if ok != 0 { Some((p.x, p.y)) } else { None }
}

/// 在托盘图标上方弹出自绘菜单（超出屏幕自动翻到内侧 / 下方）
#[tauri::command]
fn open_menu(app: tauri::AppHandle) -> Result<(), String> {
    let win = app.get_webview_window("menu").ok_or("菜单窗口没建好")?;
    let size = win.outer_size().map_err(|e| e.to_string())?;
    let cursor = cursor_pos();
    let (mut x, mut y) = match cursor {
        Some((cx, cy)) => (cx - size.width as i32 + 12, cy - size.height as i32 - 10),
        None => (0, 0),
    };

    if let (Some((cx, cy)), Ok(monitors)) = (cursor, win.available_monitors()) {
        for m in monitors {
            let p = m.position();
            let s = m.size();
            let (l, t) = (p.x, p.y);
            let (r, b) = (p.x + s.width as i32, p.y + s.height as i32);
            if cx >= l && cx < r && cy >= t && cy < b {
                if x + size.width as i32 > r { x = r - size.width as i32 - 6; }
                if x < l + 6 { x = l + 6; }
                if y < t + 6 { y = cy + 10; }
                if y + size.height as i32 > b { y = b - size.height as i32 - 6; }
                break;
            }
        }
    }

    win.set_position(tauri::PhysicalPosition::new(x, y))
        .map_err(|e| e.to_string())?;
    win.show().map_err(|e| e.to_string())?;
    if let Some(s) = app.try_state::<MenuOpenedAt>() {
        if let Ok(mut g) = s.0.lock() {
            *g = Some(std::time::Instant::now());
        }
    }
    win.set_focus().map_err(|e| e.to_string())
}

/// 用资源管理器打开日志文件（日志窗口的「打开文件」）
///
/// 只允许"程序目录下的 .log 文件"：这个命令把路径直接交给 explorer，而
/// explorer 遇到 .exe / .lnk 会执行它 —— 等于一个任意程序执行原语。
/// 页面一旦被注入（CSP 已收紧，但 eval 注入面还在），这就是提权跳板。
#[tauri::command]
fn open_logfile(path: String) -> Result<(), String> {
    let p = std::path::PathBuf::from(&path);
    let exe_dir = std::env::current_exe()
        .map_err(|e| e.to_string())?
        .parent()
        .map(|d| d.to_path_buf())
        .ok_or("拿不到程序目录")?;
    let canon_p = p.canonicalize().map_err(|_| "日志文件不存在".to_string())?;
    let canon_dir = exe_dir.canonicalize().map_err(|e| e.to_string())?;
    let is_log = canon_p
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.eq_ignore_ascii_case("log"))
        .unwrap_or(false);
    if !is_log || canon_p.parent() != Some(canon_dir.as_path()) {
        return Err("只能打开程序目录下的 .log 文件".into());
    }
    std::process::Command::new(explorer_exe())
        .arg(&canon_p)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 打开内置日志窗口（可滚动、自动跟随）
#[tauri::command]
fn open_logs(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("logs") {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
        // 之前被 __pause 停掉的轮询要在重新显示时恢复
        let _ = win.eval("window.__resume && window.__resume();");
    }
    Ok(())
}

/// 打开设置窗口（命令和托盘菜单共用）
fn open_settings_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("settings") {
        let _ = win.show();
        let _ = win.center();
        let _ = win.set_focus();
        let _ = win.eval("window.__refresh && window.__refresh();");
    }
}

/// 给后端发一个空 POST（路径自己带）
fn trigger_post(path: &str) {
    use std::io::Write;
    if let Ok(mut s) = std::net::TcpStream::connect(("127.0.0.1", 17650)) {
        let req = format!(
            "POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        );
        let _ = s.write_all(req.as_bytes());
    }
}

/// 鼠标按键是否按下（物理键状态，和焦点无关）
#[cfg(windows)]
/// 左键状态：(现在按着, 自上次调用以来按过)。
///
/// 低位 0x0001 是 GetAsyncKeyState 的"自上次询问以来按下过"，读一次就被清掉。
/// 只用 0x8000 的话，45ms 一轮的轮询会漏掉"按下又松开"的极短点击。
fn mouse_state(vk: i32) -> (bool, bool) {
    extern "system" {
        fn GetAsyncKeyState(vk: i32) -> i16;
    }
    let state = unsafe { GetAsyncKeyState(vk) } as u16;
    (state & 0x8000 != 0, state & 0x0001 != 0)
}

/// 光标是否落在我们某个可见窗口里
#[cfg(windows)]
fn cursor_in_any_window(app: &tauri::AppHandle) -> bool {
    let (cx, cy) = match cursor_pos() {
        Some(p) => p,
        None => return true, // 拿不到光标就别乱关
    };
    for w in app.webview_windows().values() {
        if !w.is_visible().unwrap_or(false) {
            continue;
        }
        if let (Ok(pos), Ok(size)) = (w.outer_position(), w.outer_size()) {
            let (x, y) = (pos.x, pos.y);
            let (w_, h_) = (size.width as i32, size.height as i32);
            if cx >= x && cx < x + w_ && cy >= y && cy < y + h_ {
                return true;
            }
        }
    }
    false
}

/// 监听鼠标：点在我们任何窗口外面 → 只收起托盘菜单（弹窗留着）
#[cfg(windows)]
fn start_outside_monitor(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        let mut pressed = false;
        loop {
            std::thread::sleep(std::time::Duration::from_millis(45));

            let any_visible = app
                .webview_windows()
                .values()
                .any(|w| w.is_visible().unwrap_or(false));
            // 每轮都读：低位是"自上次询问以来按下过"，读一次就清。
            // 只在窗口可见时读的话，隐藏期间的点击会攒着，等窗口一出现就被误判成
            // "刚点了外面"，把刚弹出来的窗立刻收掉。
            let (down, since) = mouse_state(0x01);   // 只认左键：右键/中键不动窗口

            if !any_visible {
                pressed = false;
                continue;
            }

            // down && !pressed = 按住的那一刻；since && !down = 两次轮询之间按完又松开的极短点击
            let clicked = (down && !pressed) || (since && !down);
            // 托盘点击是"按完又松开"，菜单会被放在光标上方 —— 这一次点击不能算点外面
            let just_opened = app
                .try_state::<MenuOpenedAt>()
                .and_then(|s| s.0.lock().ok().and_then(|g| *g))
                .map(|t| t.elapsed() < std::time::Duration::from_millis(300))
                .unwrap_or(false);
            if clicked && !just_opened {
                if !cursor_in_any_window(&app) {
                    // 只收托盘菜单：翻译弹窗/设置/日志留着，用户自己点 ✕ 关
                    if let Some(menu) = app.get_webview_window("menu") {
                        if menu.is_visible().unwrap_or(false) {
                            log_line("点菜单外面，收起菜单");
                            let _ = menu.hide();
                        }
                    }
                    pressed = true;   // 这一下别重复触发
                    continue;
                }
            }
            pressed = down;
        }
    });
}

/// 提示条窗口做成"点击穿透"：它只负责显示，永远不需要点。
/// 不加这个的话，提示条透明/隐藏后那块区域仍然是一个窗口，玩家在游戏里点过去会丢焦点。
#[cfg(windows)]
fn make_click_through(window: &WebviewWindow) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_NOACTIVATE,
        WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT,
    };
    if let Ok(hwnd) = window.hwnd() {
        let h = hwnd.0;
        unsafe {
            let ex = GetWindowLongPtrW(h, GWL_EXSTYLE);
            let add = (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED) as isize;
            SetWindowLongPtrW(h, GWL_EXSTYLE, ex | add);
        }
    }
}

/// 建托盘图标：左键、右键都弹自绘菜单
fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let icon = match app.default_window_icon().cloned() {
        Some(i) => i,
        None => {
            log_line("拿不到图标资源，托盘建不起来（只剩热键可用）");
            return Ok(());
        }
    };

    TrayIconBuilder::with_id("main")
        .icon(icon)
        .tooltip("Aster 聊天翻译")
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button, button_state, .. } = event {
                if button_state == MouseButtonState::Up
                    && (button == MouseButton::Left || button == MouseButton::Right)
                {
                    let _ = open_menu(tray.app_handle().clone());
                }
            }
        })
        .build(app)?;
    Ok(())
}

/// 后端 HTTP 端口是否有人应答（用来区分"sidecar 没起来"和"后端是别人起的"）
fn backend_reachable() -> bool {
    std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], 17650)),
        std::time::Duration::from_millis(300),
    )
    .is_ok()
}

/// 盯着 sidecar：它一退出就要有声音。
/// 没有这一步的话，后端死了之后窗口、托盘、设置全都正常，只有热键没反应，
/// 用户不会知道是后端挂了（release 没控制台，eprintln 全丢）。
fn watch_sidecar(app: tauri::AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(std::time::Duration::from_secs(2));
        let holder = app.state::<Sidecar>();
        let exited = {
            let mut guard = match holder.0.lock() {
                Ok(g) => g,
                Err(_) => return,
            };
            match guard.as_mut() {
                Some(c) => matches!(c.try_wait(), Ok(Some(_))),
                None => return,
            }
        };
        if exited {
            log_line("后端进程已退出，翻译功能不可用（需要重启程序）");
            toast_text(&app, "后端进程已退出，翻译不可用，请重启助手", 8000);
            return;
        }
    });
}

fn main() {
    // 先问一句"是不是已经有实例在跑"。第二份不拉后端：
    // 它会在 1~3 秒后因为端口占用自己退出，白烧一次 Python 启动。
    let primary = !already_running();
    let child = if primary { start_sidecar() } else { None };
    // Job 在 spawn 之后立刻建：万一 build() 失败退出，也能立刻收掉后端，
    // 不用等后端自己发现父进程没了（那有几秒延迟，端口还占着）。
    #[cfg(windows)]
    let job = child.as_ref().and_then(make_kill_on_close_job);
    #[cfg(not(windows))]
    let job: Option<isize> = None;
    let sidecar = std::sync::Arc::new(Mutex::new(child));

    let built = tauri::Builder::default()
        // 单实例：重复双击不会开第二份，而是把已有的设置窗口叫出来
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            open_settings_window(app);
        }))
        .manage(Sidecar(sidecar.clone()))
        .manage(Job(Mutex::new(job)))
        .manage(LastDpr(Mutex::new(1.0)))
        .manage(MenuOpenedAt(Mutex::new(None)))
        .setup(|app| {
            // 后端和 Job 都已在 main() 里建好，这里只检查它还在不在
            // 后端没起来时，窗口/托盘/设置全都正常，只有翻译是死的 —— 必须让人看见。
            // 开发跑法（target\debug 旁边没有 core\）是手动起后端，所以只在 release 弹框。
            let has_sidecar = app
                .state::<Sidecar>()
                .0
                .lock()
                .map(|g| g.is_some())
                .unwrap_or(false);
            if has_sidecar {
                watch_sidecar(app.handle().clone());
            } else {
                log_line("后端没启动（core\\AsterCore.exe 不存在或启动失败）");
                // 开发跑法是手动起后端（target\debug 旁边没有 core\），
                // 所以先看看端口在不在，别对着一个正常运行的后端喊"没启动"
                if !backend_reachable() {
                    // 这个时间点页面还没加载完，提示条 eval 会失败（toast_text 会返回 false）。
                    // 等 1.5 秒再发一次，那时 toast 页面已经就绪。
                    let handle = app.handle().clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_millis(1500));
                        toast_text(&handle, "后端没有启动，翻译不可用（检查 core 目录）", 5000);
                    });
                }
            }
            // 提示条：不接收鼠标（透明时也点不到，不会抢游戏焦点）
            if let Some(toast) = app.get_webview_window("toast") {
                #[cfg(windows)]
                make_click_through(&toast);
            }

            // 托盘图标：这里不能用 `?` —— setup 返回 Err 会让 Tauri 直接 panic，
            // release 下没有控制台，表现为"双击没反应"。开机自启时 Explorer 未就绪
            // 最容易踩到。降级 + 写日志 + 提示条，至少让程序活着。
            if let Err(e) = build_tray(app.handle()) {
                log_line(&format!("托盘创建失败：{e}（只剩热键可用）"));
                toast_text(app.handle(), "托盘图标创建失败，请重启助手", 6000);
            }

            // 调试钩子：WOWTR_SHOW_MENU=1 时启动后自动弹一次菜单。
            // 只在 debug 构建里编译进去，发布包不该带调试入口。
            #[cfg(debug_assertions)]
            if std::env::var("WOWTR_SHOW_MENU").is_ok() {
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(2500));
                    let _ = open_menu(handle);
                });
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            show_popup,
            hide_popup,
            fit_window,
            open_settings,
            quit_app,
            show_toast,
            show_notice,
            close_self,
            start_drag,
            get_autostart,
            set_autostart,
            open_selector,
            close_selector,
            open_logs,
            open_menu,
            open_logfile
        ])
        // 无边框窗口被 Alt+F4 关掉走的是 destroy：窗口没了，之后热键推数据
        // 没有任何窗口可显示，只能重启程序（实测）。统一改成"隐藏"。
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    // 日志窗只是 hide()，页面里的 1.2s 轮询不会自己停，先让它暂停
                    if window.label() == "logs" {
                        if let Some(w) = window.app_handle().get_webview_window("logs") {
                            let _ = w.eval("window.__pause && window.__pause();");
                        }
                    }
                    let _ = window.hide();
                }
                // 设置 / 日志窗口不再"永远置顶"（一直压在游戏上面很烦），
                // 改成「谁在前台谁置顶」：点进来时浮到最上面，切走之后退回普通层级。
                // 这样从托盘打开时不会被全屏游戏盖住，平时又不会赖在最上层。
                tauri::WindowEvent::Focused(focused) => {
                    let label = window.label();
                    if label == "settings" || label == "logs" {
                        let _ = window.set_always_on_top(*focused);
                    }
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!());

    // 原来是 .expect("Tauri 启动失败")：GUI 子系统里 panic = 进程静默消失，
    // 用户只看到"双击没反应"。这里改成写日志 + 弹框。
    let app = match built {
        Ok(a) => a,
        Err(e) => {
            log_line(&format!("外壳启动失败：{e}"));
            // 后端已经拉起来了：显式收掉，别让它占着 17650 等父进程超时
            if let Ok(mut g) = sidecar.lock() {
                if let Some(mut c) = g.take() {
                    let _ = c.kill();
                }
            }
            // 唯一的例外：走到这里说明窗口一个都没建起来，提示条也不存在，
            // 只能用系统消息框。除此之外一律用自绘 UI。
            alert(
                "Aster 聊天翻译",
                &format!("程序启动失败：{e}\n\n常见原因：WebView2 运行时缺失或被策略禁用、\n托盘图标创建失败。"),
            );
            std::process::exit(1);
        }
    };

    // v0.1.2：点窗口外面**不再**收起翻译弹窗/设置/日志（玩游戏时随手点一下地面就没了，
    // 实测很难受）。只保留托盘菜单的"点外面关掉" —— 那是菜单该有的行为。
    #[cfg(windows)]
    start_outside_monitor(app.handle().clone());

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            // 显式移除托盘：正常退出时 Tauri 会兜底，但强杀/异常路径会留下幽灵图标
            let _ = app_handle.remove_tray_by_id("main");
            if let Ok(mut g) = app_handle.state::<Sidecar>().0.lock() {
                if let Some(mut child) = g.take() {
                    let _ = child.kill();
                }
            }
        }
    });
}
