# -*- coding: utf-8 -*-
"""打绿色版 zip（解压即用，无需安装）。

用法：py tools/make_release.py

流程：
  1. PyInstaller 打后端 → dist/AsterCore/
  2. cargo build --release 打外壳 → target/release/*.exe
  3. 装配 dist-app/：exe + core/ + licenses/ + 使用说明
  4. 打 zip → dist-release/

会主动拦截：install 目录里混进 config.json（含明文 API Key）或运行时日志就直接报错。
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "dist-app")
OUT_DIR = os.path.join(ROOT, "dist-release")
CARGO = os.path.expanduser(r"~\.cargo\bin\cargo.exe")

VERSION = "0.1.2"
ZIP_NAME = f"AsterChatTranslator-{VERSION}-win64.zip"
TOP = "AsterChatTranslator"        # zip 内的顶层目录（纯 ASCII、不带连字符）
EXE_NAME = "Aster聊天翻译.exe"      # 用户看到的 exe 名（与使用说明一致，不带空格）

# 使用说明正文（改这里会直接进发布包）
README_TXT = """Aster 聊天翻译 v{ver}
=========================

截下游戏聊天窗口，翻成中文。解压后双击 Aster聊天翻译.exe 即可运行，无需安装。

【首次使用】
1. 双击 exe，右下角托盘出现星形图标
2. 右键托盘 → 设置
3. 「翻译区域」→ 重新框选，框住游戏聊天窗口
4. 选「翻译模式」：AI 模型（默认，需要 Key）或 本地词库（不需要 Key）
5. 用 AI 模式的话，在「API Key」里填好（支持 DeepSeek 等 OpenAI 兼容接口），点保存
6. 按下快捷键实时翻译；另一个快捷键看上次翻译（默认小键盘 + / −，可在设置里改）

【翻译模式：AI 模型 / 本地词库】
设置 → 翻译模式：
· AI 模型（默认）：看图直译，需要联网 + API Key，译文完整准确。
· 本地词库：截屏后用内置术语表逐词对照，不联网、不要 Key、不花钱；
  但只给关键词大意（中英混杂、语序可能乱），不是完整翻译。
切换后点「保存」立刻生效；本地词库模式下「我想说」用不了。

【注意】
· AI 模式需要联网：截图会发到你填的接口（含其他玩家名字与聊天内容）
· 本地词库模式完全离线，但只给关键词大意，不是完整翻译
· 没填 API Key 时只有本地词库模式可用；「我想说」必须用 AI 模式
· API Key 明文保存在 config.json，请勿把整个文件夹发给别人
· 只适配《魔兽世界》的聊天格式，其它游戏暂不支持
· 需要 WebView2 运行时（Win10/11 一般已自带）

【许可】
见 licenses\\ 目录。

【问题反馈】
https://github.com/AsterSpark/aster-chat-translator/issues

【如果想支持我或者关注更多动态】
{afdian}
"""

# 发布渠道地址：定了之后填这里，使用说明会自动带上；留空则整段不出现
AFDIAN = "https://afdian.com/a/AsterSpark"
DOWNLOAD = ""    # 例如 网盘分享链接 / GitHub Releases 地址


def run(cmd, cwd=None, env=None):
    print("  $", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env)
    if r.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {r.returncode}）：{cmd[0]}")


def build():
    print("=== 1/4 后端（PyInstaller）===")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "AsterCore.spec"], cwd=ROOT)

    print("=== 2/4 外壳（cargo release）===")
    # 关键：把构建机路径从产物里抹掉。
    #   · strip=true 去不掉 panic 消息里的文件路径（那是字符串常量）
    #   · tauri 的 generate_context! 还会记下 CARGO_MANIFEST_DIR
    # 所以两件事一起做：
    #   1) 从中性路径 C:\aster-build 构建（junction 指回真实目录），
    #      这样记录的是 C:\aster-build\src-tauri 而不是带用户名的真实路径
    #   2) RUSTFLAGS --remap-path-prefix 再把这些前缀改写成 ~
    neutral = r"C:\aster-build"
    os.makedirs(neutral, exist_ok=True)
    for sub in ("src-tauri", "app"):
        link = os.path.join(neutral, sub)
        if os.path.exists(link):
            subprocess.run(["cmd", "/c", "rmdir", link], capture_output=True)
        subprocess.run(["cmd", "/c", "mklink", "/J", link,
                        os.path.join(ROOT, sub)], capture_output=True)

    env = dict(os.environ)
    env["RUSTFLAGS"] = (f"--remap-path-prefix={ROOT}=~ "
                        f"--remap-path-prefix={os.path.expanduser('~')}=~ "
                        f"--remap-path-prefix={neutral}=~")
    env["CARGO_TARGET_DIR"] = os.path.join(ROOT, "src-tauri", "target")
    run([CARGO, "build", "--release"],
        cwd=os.path.join(neutral, "src-tauri"), env=env)


def assemble():
    print("=== 3/4 装配 dist-app ===")
    if os.path.isdir(APP):
        shutil.rmtree(APP)
    os.makedirs(os.path.join(APP, "licenses"), exist_ok=True)

    exe = os.path.join(ROOT, "src-tauri", "target", "release",
                       "aster-chat-translator.exe")
    # 发布时改成好认的中文名（cargo 产物名不能随便改，这里复制时改名）
    shutil.copy2(exe, os.path.join(APP, EXE_NAME))
    shutil.copytree(os.path.join(ROOT, "dist", "AsterCore"),
                    os.path.join(APP, "core"))
    # 发布包只带程序本体，不含任何模型目录

    for src, dst in (("LICENSE", "LICENSE.txt"),
                     ("THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.md"),
                     ("PRIVACY.md", "PRIVACY.md")):
        shutil.copy2(os.path.join(ROOT, src),
                     os.path.join(APP, "licenses", dst))

    # 绿色包下载地址（填了会写进使用说明）
    tail = ""
    if DOWNLOAD:
        tail = f"\n【更新下载】\n{DOWNLOAD}\n"

    with open(os.path.join(APP, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(README_TXT.format(ver=VERSION, afdian=AFDIAN or "（暂未开通）") + tail)

    # 关键防护：本机运行时文件绝不能进发布包
    bad = [n for n in os.listdir(APP)
           if n in ("config.json", "runtime.log", "last_result.json",
                    "capture_preview.png")]
    if bad:
        raise SystemExit(f"发布目录里混进了本机文件，先删掉：{bad}")


def pack():
    print("=== 4/4 打 zip ===")
    os.makedirs(OUT_DIR, exist_ok=True)
    zpath = os.path.join(OUT_DIR, ZIP_NAME)
    if os.path.exists(zpath):
        os.remove(zpath)

    files = []
    for dirpath, _, names in os.walk(APP):
        for n in names:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, APP)
            files.append((full, os.path.join(TOP, rel)))

    total = sum(os.path.getsize(f) for f, _ in files)
    print(f"  {len(files)} 个文件，{total / 1048576:.0f} MB 未压缩")

    done = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for full, arc in files:
            z.write(full, arc)
            done += 1
            if done % 200 == 0:
                print(f"    {done}/{len(files)}")

    size = os.path.getsize(zpath)
    print(f"\n=== 完成: {zpath}")
    print(f"    {size / 1048576:.0f} MB（压缩率 {size / total * 100:.0f}%）")


if __name__ == "__main__":
    # --repack：跳过编译，只重新装配 + 压缩（改声明文件、说明文字时用，省几分钟）
    if "--repack" not in sys.argv:
        build()
    assemble()
    pack()
