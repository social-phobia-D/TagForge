"""frozen(exe) 模式下，重引擎运行所需的独立 Python 运行时（按需安装）。

结构：
  <数据目录>/runtime/python.exe      内嵌 Python
  <数据目录>/runtime/Lib/site-packages  pip 安装的引擎依赖
  打包数据目录/_MEIPASS 内含 app/ 源码副本（spec 中作为数据文件打包），
  sidecar worker 由内嵌/用户 Python 运行，app 包路径由 worker 自己
  append 到 sys.path 末尾（绝不用 PYTHONPATH，避免遮蔽 stdlib）。
"""
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile

PY_VER = "3.12.8"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu126"

# Windows 下隐藏子进程控制台窗口；POSIX 上 creationflags 必须为 0（非 0 直接 ValueError）
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# sidecar 单次请求的默认等待上限（秒）。大模型加载/慢图也远用不了这么久，
# 超时说明子进程出了问题，宁可报错让上层重启，也不能永久卡死批量线程。
SIDECAR_TIMEOUT = 600


def runtime_dir() -> str:
    from app.engines.base import get_data_root
    return os.path.join(get_data_root(), "runtime")


def runtime_python() -> str:
    exe = "python.exe" if os.name == "nt" else os.path.join("bin", "python")
    return os.path.join(runtime_dir(), exe)


def site_packages() -> str:
    if os.name == "nt":
        return os.path.join(runtime_dir(), "Lib", "site-packages")
    import glob
    cands = glob.glob(os.path.join(runtime_dir(), "lib", "python*", "site-packages"))
    return cands[0] if cands else os.path.join(runtime_dir(), "lib", "site-packages")


def runtime_ready() -> bool:
    return os.path.exists(runtime_python()) and os.path.exists(
        os.path.join(site_packages(), "pip"))


# 用户自定义 Python 环境（路径来自设置，只读复用，绝不向其安装任何包）
_CHECK_CACHE: dict = {}  # (python_exe, engine.key) -> 缺失模块列表

# 只做模块定位（find_spec 不执行模块），毫秒级
_CHECK_SCRIPT = """
import importlib.metadata as _md
import importlib.util as _u, json, sys
if sys.version_info < (3, 10):
    print(json.dumps({"ok": False, "reason": "python<3.10"}))
else:
    args = sys.argv[1:]
    split = args.index("--") if "--" in args else len(args)
    modules = args[:split]
    specs = args[split + 1:]
    missing = [m for m in modules if _u.find_spec(m) is None]
    for spec in specs:
        if "==" not in spec:
            continue
        name, expected = spec.split("==", 1)
        try:
            actual = _md.version(name)
        except _md.PackageNotFoundError:
            actual = None
        if actual != expected and name not in missing:
            missing.append(name)
    print(json.dumps({"ok": not missing, "missing": missing}))
"""


def custom_python() -> str:
    """用户在引擎管理里填写的 python.exe 路径（main.py 从设置注入环境变量）"""
    p = os.environ.get("DABIAO_SIDECAR_PYTHON", "").strip()
    return p if p and os.path.exists(p) else ""


def check_python_deps(python_exe: str, engine) -> list:
    """检查该解释器是否满足引擎依赖，返回缺失模块列表（空=满足）"""
    cache_key = (python_exe, engine.key)
    if cache_key in _CHECK_CACHE:
        return _CHECK_CACHE[cache_key]
    mods = list(engine.import_deps or
                [p.replace("-", "_") for p in engine.pip_deps])
    missing = list(mods)
    specs = [p for p in engine.pip_deps if "==" in p]
    try:
        r = subprocess.run(
            [python_exe, "-c", _CHECK_SCRIPT] + mods + ["--"] + specs,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, creationflags=CREATE_NO_WINDOW)
        lines = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
        info = json.loads(lines[-1]) if lines else {}
        if info.get("ok"):
            missing = []
        elif "missing" in info:
            missing = list(info["missing"])
    except Exception:
        pass
    _CHECK_CACHE[cache_key] = missing
    return missing


def custom_python_ready(engine) -> bool:
    exe = custom_python()
    return bool(exe) and not check_python_deps(exe, engine)


def invalidate_dep_cache(python_exe: str = None):
    """依赖状态缓存失效。安装完成后必须调用，否则界面会一直显示
    「需安装依赖」直到重启（缓存永不更新）。python_exe 为 None 时全清。"""
    if python_exe is None:
        _CHECK_CACHE.clear()
        return
    for key in [k for k in _CHECK_CACHE if k[0] == python_exe]:
        _CHECK_CACHE.pop(key, None)


def install_target() -> str:
    """依赖安装目标：'runtime'（程序自带运行时，默认）或 'custom'（用户指定环境）"""
    return os.environ.get("DABIAO_INSTALL_TARGET", "runtime").strip() or "runtime"


def _runtime_marker_ok(engine) -> bool:
    if not runtime_ready():
        return False
    return not check_python_deps(runtime_python(), engine)


def sidecar_deps_ready(engine) -> bool:
    """frozen 下判断引擎依赖是否就绪（按安装目标）"""
    if install_target() == "custom":
        return custom_python_ready(engine)
    if _runtime_marker_ok(engine):
        return True
    return custom_python_ready(engine)  # 自定义环境只读兜底


def current_python(engine) -> str:
    """sidecar 实际使用的解释器（与依赖就绪判断同一优先级）"""
    exe = custom_python()
    custom_ok = bool(exe) and (exe, engine.key) in _CHECK_CACHE \
        and not _CHECK_CACHE[(exe, engine.key)]
    if install_target() == "custom":
        return exe if custom_ok else runtime_python()
    if _runtime_marker_ok(engine):
        return runtime_python()
    return exe if custom_ok else runtime_python()


def _missing_pip_packages(engine, exe) -> list:
    """按 pip_deps/import_deps 对应关系，把缺失模块映射回 pip 包名"""
    missing = set(check_python_deps(exe, engine))
    names = engine.import_deps or [p.replace("-", "_") for p in engine.pip_deps]
    return [pip_name for pip_name, mod in zip(engine.pip_deps, names)
            if mod in missing]


def _bundle_root() -> str:
    """frozen 时打包数据文件所在目录（PyInstaller 6 = _internal）"""
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)


def worker_script() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(_bundle_root(), "app", "engines", "sidecar_worker.py")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app", "engines", "sidecar_worker.py")


def app_root_for_worker() -> str:
    """sidecar python 的 sys.path 根目录（app 包所在处）。
    仅文档用途；路径由 sidecar_worker 自己 append，禁止走 PYTHONPATH。"""
    if getattr(sys, "frozen", False):
        return _bundle_root()
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def download_file(url: str, dest: str, log_cb=None, timeout: int = 30,
                  progress_cb=None):
    """公开的分块下载（带超时），供各引擎下载权重复用"""
    _download(url, dest, log_cb, timeout, progress_cb=progress_cb)


def _download(url: str, dest: str, log_cb=None, timeout: int = 30,
              attempts: int = 6, progress_cb=None):
    """分块下载：断点续传 + 自动重试。网络差/限速时单次请求会超时中断，
    用 Range 从 .part 已有字节继续；先写 .part 再改名，失败不残留半截
    文件被误判为权重已就绪。有 Content-Length 时回报真实字节百分比。"""
    if log_cb:
        log_cb(f"下载 {url} ...")
    d = os.path.dirname(dest)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = dest + ".part"
    last = None
    for _ in range(attempts):
        try:
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            headers = {"User-Agent": "TagForge/1.0"}
            if have:
                headers["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                mode = "ab" if (have and resp.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                total = _content_length(resp, have)
                written = have
                last_pct = -10
                logged_pct = -10
                with open(tmp, mode) as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                        if total:
                            pct = min(99, int(written * 100 / total))
                            if progress_cb and pct - last_pct >= 1:
                                last_pct = pct
                                progress_cb(pct)
                            if log_cb and pct // 10 > logged_pct // 10:
                                logged_pct = pct
                                log_cb(f"  下载中 {pct}%")
            os.replace(tmp, dest)
            if progress_cb:
                progress_cb(100)
            return
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 416 and os.path.exists(tmp) \
                    and os.path.getsize(tmp) > 0:
                os.replace(tmp, dest)  # 已下载完成（Range 超出总长）
                if progress_cb:
                    progress_cb(100)
                return
        except Exception as e:
            last = e
        if log_cb:
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            log_cb(f"  网络中断，从 {have} 字节处继续重试…")
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise last


def _content_length(resp, have: int):
    """本次响应的目标总字节数（含断点续传前已有的部分），拿不到就返回 None"""
    cr = resp.headers.get("Content-Range")
    if cr and "/" in cr:
        try:
            return int(cr.rsplit("/", 1)[1])
        except ValueError:
            pass
    cl = resp.headers.get("Content-Length")
    if cl:
        try:
            return int(cl) + have
        except ValueError:
            pass
    return None


def _has_nvidia() -> bool:
    import shutil
    return bool(shutil.which("nvidia-smi"))


def _pip_cmd(python_exe: str, packages: list, index: str = None) -> list:
    cmd = [python_exe, "-m", "pip", "install", "--prefer-binary"] + packages
    if index:
        cmd += ["--index-url", index]
    return cmd


def _run_pip(cmd: list, log_cb=None) -> bool:
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW)
        for line in proc.stdout:
            if log_cb:
                log_cb(line.rstrip())
        proc.wait()
        return proc.returncode == 0
    except Exception as e:
        if log_cb:
            log_cb(f"pip 执行失败: {e}")
        return False


def install_engine_deps(python_exe: str, packages: list, log_cb=None) -> bool:
    """torch 类依赖优先装 CUDA 版（有 N 卡时），其余从 PyPI 装"""
    pkgs = list(dict.fromkeys(packages))
    torch_like = [p for p in pkgs if p in ("torch", "torchvision")]
    rest = [p for p in pkgs if p not in torch_like]
    if torch_like:
        if _has_nvidia():
            if log_cb:
                log_cb("检测到 NVIDIA 显卡，安装 CUDA 版 PyTorch（体积大，请耐心等待）...")
            if not _run_pip(_pip_cmd(python_exe, torch_like, TORCH_CUDA_INDEX), log_cb):
                if log_cb:
                    log_cb("CUDA 版安装失败，回退 CPU 版 ...")
                if not _run_pip(_pip_cmd(python_exe, torch_like), log_cb):
                    return False
        else:
            if not _run_pip(_pip_cmd(python_exe, torch_like), log_cb):
                return False
    if rest:
        if not _run_pip(_pip_cmd(python_exe, rest), log_cb):
            return False
    return True


def setup_runtime(log_cb=None) -> bool:
    """安装 sidecar 运行时。Windows=内嵌 Python；Linux/macOS=系统 python3 建 venv。
    已装好则直接返回 True。"""
    if runtime_ready():
        return True
    if os.name != "nt":
        return _setup_runtime_posix(log_cb)
    d = runtime_dir()
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "_embed.zip")
    try:
        _download(EMBED_URL, tmp, log_cb)
        if log_cb:
            log_cb("解压 Python 运行时 ...")
        with zipfile.ZipFile(tmp) as z:
            z.extractall(d)
        os.remove(tmp)

        # 修改 python3xx._pth：启用 site-packages 与 site
        import glob
        cands = glob.glob(os.path.join(d, "python*._pth"))
        if cands:
            pth = cands[0]
            with open(pth, "r", encoding="utf-8") as f:
                content = f.read()
            content = content.replace("#import site", "import site")
            if "Lib/site-packages" not in content:
                content += "\nLib/site-packages\n"
            with open(pth, "w", encoding="utf-8") as f:
                f.write(content)

        getpip = os.path.join(d, "get-pip.py")
        _download(GET_PIP_URL, getpip, log_cb)
        if log_cb:
            log_cb("安装 pip ...")
        r = subprocess.run([runtime_python(), getpip, "--no-warn-script-location"],
                           capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        if os.path.exists(getpip):
            os.remove(getpip)
        if r.returncode != 0:
            raise RuntimeError(f"pip 安装失败: {r.stderr[-2000:]}")
        if log_cb:
            log_cb("Python 运行时安装完成。")
        return True
    except Exception as e:
        if log_cb:
            log_cb(f"运行时安装失败: {e}")
        return False


def _setup_runtime_posix(log_cb=None) -> bool:
    """Linux/macOS：用系统 python3 在数据目录创建 venv（自带 pip，无需 get-pip）。"""
    import shutil
    base = shutil.which("python3") or shutil.which("python")
    if not base:
        if log_cb:
            log_cb("未找到系统 python3，无法创建运行时。请先安装 Python 3.10+。")
        return False
    d = runtime_dir()
    os.makedirs(d, exist_ok=True)
    if log_cb:
        log_cb(f"使用 {base} 创建独立运行时 (venv) ...")
    r = subprocess.run([base, "-m", "venv", d], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(runtime_python()):
        if log_cb:
            log_cb(f"venv 创建失败: {(r.stderr or r.stdout or '')[-2000:]}")
        if log_cb:
            log_cb("提示: Debian/Ubuntu 需先安装 python3-venv 包。")
        return False
    if log_cb:
        log_cb("Python 运行时创建完成。")
    return True


def ensure_engine_runtime(engine, log_cb=None) -> bool:
    """frozen 模式：按安装目标装依赖。
    custom=装进用户指定的环境（用户在界面明确选择）；runtime=装进程序自带运行时。
    runtime 目标下若自定义环境依赖已齐全，则只读复用、零下载。"""
    if install_target() == "custom":
        exe = custom_python()
        if not exe:
            if log_cb:
                log_cb("未填写自定义 Python 环境路径。请填写路径，或把安装目标切回「程序自带运行时」。")
            return False
        missing = _missing_pip_packages(engine, exe)
        extras = ["torchvision", "huggingface_hub", "pillow", "numpy"]
        pkgs = list(dict.fromkeys(missing + extras))
        if log_cb:
            log_cb(f"安装目标：自定义环境 {exe}")
            log_cb(f"将安装（已装过的 pip 会自动跳过）: {', '.join(pkgs)}")
        ok = install_engine_deps(exe, pkgs, log_cb)
        if ok:
            invalidate_dep_cache(exe)  # 装完立刻刷新状态，别让旧缓存骗人
        return ok

    exe = custom_python()
    if exe and custom_python_ready(engine):
        if log_cb:
            log_cb(f"自定义环境依赖齐全，只读复用：{exe}（不做任何修改）")
        return True
    if not setup_runtime(log_cb):
        return False
    pkgs = list(dict.fromkeys(
        engine.pip_deps + ["torchvision", "huggingface_hub", "pillow", "numpy"]))
    ok = install_engine_deps(runtime_python(), pkgs, log_cb)
    if ok:
        invalidate_dep_cache(runtime_python())
    return ok


class SidecarProcess:
    """与 sidecar_worker.py 的 JSON-lines 协议封装"""

    def __init__(self, engine, log_cb=None):
        self.engine_key = engine.key
        self.log_cb = log_cb
        # 当前请求的进度回调（0-100）。注意它由 _read_loop 线程调用，
        # 上层传进来的必须是 Qt 信号 emit（跨线程排队）之类的线程安全函数。
        self.progress_cb = None
        self.is_loaded = False
        self._event = threading.Event()
        self._reply = None
        self._exited = False
        from app.engines.base import get_data_root
        # worker 的 stderr 走文件：console=False 打包下无处可显示，而库的进度
        # （下载 CLIP、tqdm）与崩溃 traceback 全在这里，丢弃就等于瞎排障。
        log_path = os.path.join(get_data_root(), "logs", f"sidecar-{engine.key}.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        if os.path.exists(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:
            try:  # 库的进度条很吵，只留上一份，防止无限增长
                os.replace(log_path, log_path + ".1")
            except OSError:
                pass
        self._err_fh = open(log_path, "a", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [current_python(engine), worker_script(), engine.key],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._err_fh, text=True, encoding="utf-8", bufsize=1,
            cwd=os.path.dirname(worker_script()),
            # 不加这项，打包版(console=False)每起一个 sidecar 就弹一个黑色 cmd 窗口
            creationflags=CREATE_NO_WINDOW,
            env={**os.environ, "PYTHONIOENCODING": "utf-8",
                 # worker 是外部 Python（sys.frozen=False），必须显式钉住数据根，
                 # 否则 get_data_root() 算出 _MEIPASS，权重会下进安装目录
                 "DABIAO_DATA_DIR": get_data_root()})
        # 注意：绝不给 worker 设 PYTHONPATH=<bundle_root>——它排在 stdlib 之前，
        # 打包目录里的 cp3xx 扩展(_socket 等)会遮蔽用户环境的同名模块，
        # 触发 "Module use of python3xx.dll conflicts"。app 包由 worker 自己
        # append 到 sys.path 末尾（sidecar_worker.py）。
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                t = msg.get("type")
                if t == "log":
                    if self.log_cb:
                        self.log_cb(msg.get("msg", ""))
                elif t == "progress":
                    if self.progress_cb:
                        try:
                            self.progress_cb(int(msg.get("value", 0)))
                        except Exception:
                            pass
                elif t == "error":
                    self._reply = {"type": "error", "msg": msg.get("msg", "")}
                    self._event.set()
                else:
                    self._reply = msg
                    self._event.set()
        except Exception:
            pass
        finally:
            # 子进程退出（崩溃/被杀）：必须唤醒等待方，否则在途请求要干等满
            # SIDECAR_TIMEOUT(600s) —— 表现就是"卡死且取消无反应"。
            self._exited = True
            if not self._event.is_set():
                self._reply = {"type": "error",
                               "msg": f"sidecar 进程已退出（详见 logs/sidecar-{self.engine_key}.log）"}
                self._event.set()

    def abort(self):
        """取消打标：杀掉子进程并立即唤醒在途请求，不等超时。"""
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass
        self._exited = True
        if not self._event.is_set():
            self._reply = {"type": "error", "msg": "已取消"}
            self._event.set()
        self.is_loaded = False

    def _request(self, payload: dict, timeout: float = SIDECAR_TIMEOUT):
        if self._exited:
            raise RuntimeError("sidecar 进程已退出")
        if not self.alive:
            raise RuntimeError("sidecar 进程未运行")
        self._event.clear()
        self._reply = None
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise RuntimeError(f"sidecar 写入失败（进程可能已退出）: {e}")
        # 无超时的等待 = 批量打标线程可能被一次子进程挂起永久卡死，
        # 且取消按钮救不了（取消只在图片间生效）。必须带上限。
        if not self._event.wait(timeout):
            if self.alive:
                self.close()
                raise RuntimeError(f"sidecar 响应超时（>{int(timeout)}s），已终止该进程")
            raise RuntimeError("sidecar 进程已退出")
        reply = self._reply or {"type": "error", "msg": "sidecar 无响应"}
        if reply["type"] == "error":
            raise RuntimeError(reply.get("msg", "sidecar 错误"))
        return reply

    def download(self, progress_cb=None):
        self.progress_cb = progress_cb
        self._request({"op": "download"})

    def load(self, params: dict, progress_cb=None):
        from app.engines.base import get_models_dir
        self.progress_cb = progress_cb
        self._request({"op": "load", "models_dir": get_models_dir(),
                       "params": params})
        self.is_loaded = True

    def tag(self, path: str, params: dict, progress_cb=None) -> dict:
        self.progress_cb = progress_cb
        return self._request({"op": "tag", "path": path, "params": params})["data"]

    def close(self):
        if self.proc is not None:
            try:
                self._request({"op": "exit"}, timeout=10)
            except Exception:
                pass
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)  # 不 wait 会短暂留僵尸进程
            except Exception:
                pass
        self.proc = None
        try:
            self._err_fh.close()
        except Exception:
            pass
