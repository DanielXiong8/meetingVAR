"""meetingVAR 安装器（自制 setup.exe，Inno 官方源防直链故自建）——三合一：

- 默认：tkinter 安装向导（选目录 → 安装 → 可选立即启动）；
- --silent --dir <path>：静默安装（自动化测试用；结果写 %TEMP%\meetingvar_setup.log）；
- --uninstall [--silent]：卸载（删文件/快捷方式/注册表项）。

安装内容 = UI 壳 meetingVAR.exe（引擎环境不在包内，首次运行在设置页指定）。
免管理员：装到 %LOCALAPPDATA%（或用户自选目录），快捷方式与卸载注册均走用户级。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import winreg
from pathlib import Path

APP_NAME = "meetingVAR"
APP_VERSION = "0.1.3"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / APP_NAME
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\meetingVAR"
LOG_FILE = Path(tempfile.gettempdir()) / "meetingvar_setup.log"


def _base() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _payload_exe() -> Path:
    frozen = _base() / "payload" / f"{APP_NAME}.exe"
    if frozen.exists():
        return frozen
    dev = _base().parent.parent / "dist" / f"{APP_NAME}.exe"  # 源码目录下测试用
    if dev.exists():
        return dev
    raise FileNotFoundError(f"安装载荷缺失: {frozen}")


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def _ps_run(script: str) -> None:
    tmp = Path(tempfile.gettempdir()) / "mv_mkshortcut.ps1"
    tmp.write_text(script, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp)],
                   capture_output=True, timeout=60)


def make_shortcuts(target_dir: Path) -> None:
    exe = target_dir / f"{APP_NAME}.exe"
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
foreach ($base in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {{
  $lnk = $ws.CreateShortcut((Join-Path $base '{APP_NAME}.lnk'))
  $lnk.TargetPath = '{exe}'
  $lnk.WorkingDirectory = '{target_dir}'
  $lnk.IconLocation = '{exe},0'
  $lnk.Description = 'meetingVAR——面向复杂工作会议的个人证据层'
  $lnk.Save()
}}
"""
    _ps_run(ps)


def remove_shortcuts() -> None:
    ps = f"""
foreach ($base in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {{
  $p = Join-Path $base '{APP_NAME}.lnk'
  if (Test-Path $p) {{ Remove-Item $p -Force }}
}}
"""
    _ps_run(ps)


def write_registry(target_dir: Path) -> None:
    exe = target_dir / f"{APP_NAME}.exe"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "meetingVAR 项目组")
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(target_dir))
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ,
                          f'"{target_dir / "uninstall.exe"}" --uninstall')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def remove_registry() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_PATH)
    except OSError:
        pass


def install(target_dir: Path, progress=None) -> None:
    def step(msg: str) -> None:
        log(f"[install] {msg}")
        if progress:
            progress(msg)

    payload = _payload_exe()
    step(f"创建目录 {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    step("复制程序文件")
    shutil.copy2(payload, target_dir / f"{APP_NAME}.exe")
    # 把安装器自身复制为卸载器（保留安装包在别处被删后仍可卸载）
    me = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    shutil.copy2(me, target_dir / "uninstall.exe")
    step("创建快捷方式（桌面 + 开始菜单）")
    make_shortcuts(target_dir)
    step("写入卸载注册表项")
    write_registry(target_dir)
    step("安装完成")


def uninstall(silent: bool = False) -> None:
    if not silent:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if not messagebox.askyesno(APP_NAME, f"卸载 {APP_NAME}？（会议数据与产物不会被删除）"):
            return
    log("[uninstall] 开始")
    subprocess.run(["taskkill", "/IM", f"{APP_NAME}.exe", "/F"], capture_output=True)
    target = DEFAULT_DIR
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
            target = Path(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        pass
    remove_registry()
    remove_shortcuts()
    if target.exists():
        for f in target.iterdir():
            try:
                if f.name != "uninstall.exe":
                    (shutil.rmtree(f) if f.is_dir() else f.unlink())
            except OSError:
                pass
        # 自删目录（本进程占用 uninstall.exe，延迟清理）
        subprocess.Popen(
            f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{target}"',
            shell=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    log("[uninstall] 完成")


def gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title(f"{APP_NAME} 安装程序 {APP_VERSION}")
    root.geometry("460x260")
    root.resizable(False, False)
    try:
        root.iconbitmap(str(_base() / "assets" / "icon.ico"))
    except Exception:  # noqa: BLE001
        pass

    tk.Label(root, text=APP_NAME, font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(18, 0))
    tk.Label(root, text="面向复杂工作会议的个人证据层 · 安装向导", fg="#666").pack()
    frame = tk.Frame(root)
    frame.pack(fill="x", padx=20, pady=14)
    tk.Label(frame, text="安装位置：").pack(anchor="w")
    row = tk.Frame(frame)
    row.pack(fill="x", pady=(4, 0))
    path_var = tk.StringVar(value=str(DEFAULT_DIR))
    tk.Entry(row, textvariable=path_var).pack(side="left", fill="x", expand=True)
    tk.Button(row, text="浏览…", command=lambda: path_var.set(
        filedialog.askdirectory(initialdir=path_var.get()) or path_var.get())).pack(side="left", padx=(6, 0))
    status = tk.Label(root, text="将安装：程序本体（引擎环境首次运行时在设置页指定）", fg="#666", wraplength=420)
    status.pack()
    launch_var = tk.BooleanVar(value=True)
    tk.Checkbutton(root, text="安装完成后立即启动", variable=launch_var).pack(pady=(6, 0))

    def do_install() -> None:
        target = Path(path_var.get().strip())
        try:
            install(target, progress=lambda m: status.config(text=m))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"安装失败：{exc}")
            return
        if launch_var.get():
            subprocess.Popen([str(target / f"{APP_NAME}.exe")])
        messagebox.showinfo(APP_NAME, "安装完成！桌面与开始菜单已创建快捷方式。\n卸载：设置→应用，或运行安装目录下的 uninstall.exe")
        root.destroy()

    tk.Button(root, text="安装", width=18, height=2, bg="#2b3440", fg="white",
              command=do_install).pack(pady=10)
    root.mainloop()


def main() -> None:
    args = sys.argv[1:]
    if "--uninstall" in args:
        uninstall(silent="--silent" in args)
        return
    if "--silent" in args:
        target = DEFAULT_DIR
        if "--dir" in args:
            target = Path(args[args.index("--dir") + 1])
        try:
            install(target)
            sys.exit(0)
        except Exception as exc:  # noqa: BLE001
            log(f"[install] 失败: {exc}")
            sys.exit(1)
    gui()


if __name__ == "__main__":
    main()
