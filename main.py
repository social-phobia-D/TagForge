import os
import sys


def _res_path():
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _setup_frozen_qt_dlls():
    """冻结版启动前显式注册 PySide6 的 DLL 目录并预加载 Qt。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    pyside = os.path.join(base, "PySide6")
    shiboken = os.path.join(base, "shiboken6")
    for folder in (shiboken, pyside, base):
        if not os.path.isdir(folder):
            continue
        try:
            os.add_dll_directory(folder)
        except OSError:
            pass
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
    try:
        import ctypes
        for folder, name in (
                (shiboken, "shiboken6.abi3.dll"),
                (pyside, "Qt6Core.dll"),
                (pyside, "pyside6.abi3.dll"),
                (pyside, "Qt6Gui.dll"),
                (pyside, "Qt6Widgets.dll")):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                ctypes.WinDLL(path)
    except OSError:
        pass


def _setup_crash_log():
    """console=False 打包后 stderr 不可见，未捕获异常会无声消失。
    把 faulthandler 与 excepthook 输出落到数据目录 logs/，用户报障有据可查。"""
    try:
        import faulthandler
        import time
        import traceback
        import threading
        from app.engines.base import get_data_root
        log_dir = os.path.join(get_data_root(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(
            log_dir, f"tagforge-{time.strftime('%Y%m%d')}.log")
        fh = open(path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(fh)

        def _write(msg):
            fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

        def _hook(tp, val, tb):
            _write("UNCAUGHT EXCEPTION\n" +
                   "".join(traceback.format_exception(tp, val, tb)))
            sys.__excepthook__(tp, val, tb)
        sys.excepthook = _hook

        def _thread_hook(args):
            _write(f"EXCEPTION IN THREAD {args.thread}\n" +
                   "".join(traceback.format_exception(
                       args.exc_type, args.exc_value, args.exc_traceback)))
        threading.excepthook = _thread_hook
        os.environ["DABIAO_LOG_FILE"] = path
    except Exception:
        pass


def main():
    _setup_frozen_qt_dlls()
    # 数据目录（模型/运行时）可选自定义，默认在程序所在盘
    try:
        from PySide6.QtCore import QSettings
        q = QSettings("Dabiao", "dabiao")
        custom = str(q.value("general/data_dir", "")).strip()
        if custom:
            os.environ["DABIAO_DATA_DIR"] = custom
        custom_py = str(q.value("general/custom_python", "")).strip()
        if custom_py:
            os.environ["DABIAO_SIDECAR_PYTHON"] = custom_py
        os.environ.setdefault("DABIAO_INSTALL_TARGET",
                              str(q.value("general/install_target", "runtime")))
    except Exception:
        pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    _setup_crash_log()
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("TagForge")
    ico = os.path.join(_res_path(), "icon.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))
    try:  # Fluent 组件库主题：暗色 + 紫色强调，与全局 QSS 一致
        from qfluentwidgets import Theme, setTheme, setThemeColor
        setTheme(Theme.DARK)
        setThemeColor("#8b5cf6")
    except Exception:
        pass
    # 平台默认中文字体；Qt 在字体缺失时会自动回退，不会报错
    if sys.platform == "darwin":
        default_font = "PingFang SC"
    elif os.name == "posix":
        default_font = "Noto Sans CJK SC"
    else:
        default_font = "Microsoft YaHei UI"
    app.setFont(QFont(default_font, 9))
    qss = os.path.join(_res_path(), "app", "ui", "theme.qss")
    if os.path.exists(qss):
        with open(qss, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    from app.ui.main_window import MainWindow
    w = MainWindow()
    _dark_titlebar(w)
    w.show()
    sys.exit(app.exec())


def _dark_titlebar(win):
    """Windows 11/10 深色标题栏，与紫黑主题一致"""
    try:
        import ctypes
        hwnd = int(win.winId())
        for attr in (20, 19):  # 20 = Win10 180+/11，19 = 旧版回退
            v = ctypes.c_int(1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0:
                break
    except Exception:
        pass


if __name__ == "__main__":
    main()
