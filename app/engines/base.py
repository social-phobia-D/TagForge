import importlib.util
import os
import sys
from dataclasses import dataclass, field

# 打包后(frozen)重引擎通过 sidecar 独立 Python 运行
FROZEN = getattr(sys, "frozen", False)


def get_data_root() -> str:
    """所有数据（模型/运行时/配置）的根目录，严禁放 C 盘用户目录：
    优先环境变量 DABIAO_DATA_DIR；打包后=exe 所在目录（Windows）；
    macOS 打包在 .app 内不可写，回退到 Application Support；
    Linux 若 exe 目录只读则回退到 ~/.local/share。源码=项目根目录"""
    env = os.environ.get("DABIAO_DATA_DIR")
    if env:
        return env
    if FROZEN:
        exe_dir = os.path.dirname(sys.executable)
        if os.access(exe_dir, os.W_OK):
            return exe_dir
        if sys.platform == "darwin":
            return os.path.join(os.path.expanduser("~"), "Library",
                                "Application Support", "TagForge")
        if os.name == "posix":
            return os.path.join(os.path.expanduser("~"), ".local",
                                "share", "TagForge")
        return exe_dir
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_models_dir() -> str:
    d = os.path.join(get_data_root(), "models")
    os.makedirs(d, exist_ok=True)
    return d


# trust_remote_code 的动态模块缓存默认写 ~/.cache/huggingface/modules（C 盘），必须重定向
os.environ.setdefault("HF_MODULES_CACHE", os.path.join(get_data_root(), "hf_modules"))
os.makedirs(os.environ["HF_MODULES_CACHE"], exist_ok=True)


def custom_weights(key: str) -> str:
    """引擎管理里用户手动指定的权重路径（路径存在才返回）。
    延迟导入 QSettings：本模块必须保持 sidecar(无 Qt 环境)可导入；
    worker 进程通过 load 参数拿到该路径，不会调用本函数。"""
    try:
        from PySide6.QtCore import QSettings
        p = QSettings("Dabiao", "dabiao").value(f"weights/{key}", "") or ""
        return p if p and os.path.exists(p) else ""
    except Exception:
        return ""


@dataclass
class Box:
    """检测框：像素坐标（原图尺寸）。放本模块保证 sidecar 无 Qt 环境可用"""
    label: str = ""
    conf: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0


@dataclass
class EngineResult:
    tags: list = field(default_factory=list)
    boxes: list = field(default_factory=list)  # list[Box]


@dataclass
class EngineStatus:
    deps_ready: bool = False
    weights_ready: bool = False


class EngineBase:
    """打标引擎基类。

    源码模式：直接进程内加载。
    frozen 模式：重引擎(need_torch)通过 sidecar 独立 Python 进程运行，
    WD14 等轻引擎仍进程内。
    """
    key = ""
    title = ""
    description = ""
    vram_note = ""
    pip_deps: list = []       # 首次使用需 pip 安装的包（pip 包名）
    import_deps: list = None  # import 检测用的模块名，缺省由 pip_deps 推断
    weights_size = ""         # 模型下载体积提示
    need_torch = False        # 是否依赖 torch（决定 frozen 模式是否走 sidecar）
    has_boxes = False         # 是否输出检测框（决定画布是否显示框）
    weight_urls: list = []    # 手动下载地址（引擎管理里展示/复制，供受限网络用户）
    custom_hint = ""          # 本地权重的期望内容说明
    custom_is_file = False    # 本地权重是单个文件(True)还是文件夹(False)

    def __init__(self):
        self._loaded = False
        self._sidecar = None
        self._last_params: dict = {}  # 供 sidecar 崩溃重启后自动重新加载

    # ---------- 状态 ----------
    def deps_installed(self) -> bool:
        if self.need_torch and FROZEN:
            from app.engines.sidecar import sidecar_deps_ready
            return sidecar_deps_ready(self)
        return not self.missing_deps()

    def missing_deps(self) -> list:
        if self.need_torch and FROZEN:
            return []
        names = self.import_deps or [p.replace("-", "_") for p in self.pip_deps]
        return [p for p, m in zip(self.pip_deps, names)
                if importlib.util.find_spec(m) is None]

    def models_dir(self) -> str:
        return get_models_dir()

    def custom_weights(self) -> str:
        return custom_weights(self.key)

    def weights_ready(self) -> bool:
        raise NotImplementedError

    @property
    def loaded(self) -> bool:
        if self.use_sidecar():
            return bool(self._sidecar and self._sidecar.alive and self._sidecar.is_loaded)
        return self._loaded

    def use_sidecar(self) -> bool:
        return self.need_torch and FROZEN

    def is_builtin(self) -> bool:
        """依赖是否随主程序内置（WD14）"""
        return not self.need_torch

    # ---------- 侧车 ----------
    def _ensure_sidecar(self, log_cb=None):
        if self._sidecar and self._sidecar.alive:
            return self._sidecar
        if self._sidecar is not None:
            try:  # 上一个进程已死，清理干净再重建
                self._sidecar.close()
            except Exception:
                pass
            self._sidecar = None
        from app.engines.sidecar import SidecarProcess
        self._sidecar = SidecarProcess(self, log_cb=log_cb)
        return self._sidecar

    # ---------- 操作（UI 线程调用，实际在 worker 线程执行） ----------
    def download(self, log_cb=None):
        """下载模型权重（依赖就绪后）"""
        cw = self.custom_weights()
        if cw:
            if log_cb:
                log_cb(f"已设置本地权重 {cw}，跳过下载")
            return
        if self.use_sidecar():
            self._ensure_sidecar(log_cb).download()
            return
        self._download_impl(get_models_dir(), log_cb)

    def load(self, params: dict, log_cb=None):
        params = dict(params or {})
        cw = self.custom_weights()
        if cw:
            params["custom_weights"] = cw  # sidecar worker 无法读设置，随参数传入
        if self.use_sidecar():
            sc = self._ensure_sidecar(log_cb)
            if not sc.is_loaded:
                sc.load(params)
            self._loaded = True
            self._last_params = dict(params or {})
            return
        self._load_impl(get_models_dir(), params, log_cb)
        self._loaded = True

    def unload(self):
        if self.use_sidecar():
            if self._sidecar:
                self._sidecar.close()
                self._sidecar = None
            self._loaded = False
            return
        self._unload_impl()
        self._loaded = False

    def tag_image(self, path: str, params: dict) -> EngineResult:
        if self.use_sidecar():
            sc = self._ensure_sidecar()
            if not sc.is_loaded:
                # sidecar 超时/崩溃后重建的进程是空白的，先恢复上次加载的模型
                sc.load(self._last_params or params)
            r = sc.tag(path, params)
            return EngineResult(tags=r.get("tags", []), boxes=r.get("boxes", []))
        return self._tag_impl(path, params)

    # ---------- 进程内实现（sidecar worker / 源码模式调用） ----------
    def _download_impl(self, models_dir: str, log_cb=None):
        raise NotImplementedError

    def _load_impl(self, models_dir: str, params: dict, log_cb=None):
        raise NotImplementedError

    def _unload_impl(self):
        pass

    def _tag_impl(self, path: str, params: dict) -> EngineResult:
        raise NotImplementedError


ENGINES: list = []


def get_engines() -> list:
    global ENGINES
    if not ENGINES:
        from app.engines.wd14 import Wd14Engine
        from app.engines.florence2 import Florence2Engine
        from app.engines.yoloworld import YoloWorldEngine
        from app.engines.locateanything import LocateAnythingEngine
        ENGINES = [Wd14Engine(), Florence2Engine(), YoloWorldEngine(), LocateAnythingEngine()]
    return ENGINES


def get_engine(key: str) -> EngineBase:
    for e in get_engines():
        if e.key == key:
            return e
    raise KeyError(key)
