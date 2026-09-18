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


def hf_snapshot_download(repo_id: str, cache_dir: str, log_cb=None,
                         progress_cb=None):
    """snapshot_download + 进度回调（0-100）。

    坑：huggingface_hub 只把 tqdm_class 给外层「按文件计数」的进度条用，
    hf_hub_download 本身根本不收这个参数；而 tqdm 的 update 又会被 miniters
    批处理合并、非 tty 下还会整个被 disable，实测一次都不回调。
    所以这里两手抓：
      - 外层：包 tqdm_class 并钩 __iter__，拿到「已完成文件数」；
      - 内层：临时替换 huggingface_hub.utils.tqdm 模块里的 tqdm 符号，
        让每个文件的字节进度条也归我们管（http_get 是显式调 update，
        不受批处理影响），把 in-flight 文件的字节比例算进去。
    合计百分比 = (已完成文件数 + Σ活动文件字节比例) / 总文件数。
    任何一步出问题都只是没有进度，绝不影响下载本身。"""
    import huggingface_hub
    if progress_cb:
        progress_cb(1)
    state = {"files": 0, "done": 0, "active": {}, "last": 0}

    def _emit():
        if not progress_cb or not state["files"]:
            return
        try:
            frac = sum(state["active"].values())
            pct = int((state["done"] + min(frac, state["files"])) * 100
                      / state["files"])
            if pct > state["last"]:
                state["last"] = pct
                progress_cb(min(99, pct))
        except Exception:
            pass

    tqdm_class = None
    patched_mod = None
    orig_tqdm = None
    try:
        import sys as _sys
        from huggingface_hub.utils import tqdm as hf_tqdm
        # 注意：`import huggingface_hub.utils.tqdm` 拿到的是**类**（包属性遮蔽了
        # 子模块），必须从 sys.modules 取真正的模块才能改到 _get_progress_bar_context
        # 用的那个全局名。
        tqmod = _sys.modules.get("huggingface_hub.utils.tqdm")

        class _OuterTqdm(hf_tqdm):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                # 在 __init__ 就记下文件总数：内层字节条是 worker 线程里建的，
                # 可能比外层开始迭代还早，晚了会丢掉最早那几次上报。
                if self.total:
                    state["files"] = self.total

            def __iter__(self):
                if self.total:
                    state["files"] = self.total
                for i, obj in enumerate(super().__iter__(), 1):
                    state["done"] = i
                    _emit()
                    yield obj

        class _FileTqdm(hf_tqdm):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self._key = None
                self._acc = 0
                try:
                    if kw.get("unit") == "B" and self.total:
                        self._key = id(self)
                        state["active"][self._key] = 0.0
                except Exception:
                    pass

            def update(self, n=1):
                r = super().update(n)
                try:
                    # 不能读 self.n / self.initial：stderr 不是 tty 时（sidecar 把
                    # stderr 重定向进日志文件）tqdm 会把 disable 置真并**提前
                    # return**，连 self.n/self.initial 都不赋。所以自己累加。
                    if self._key is not None and self.total:
                        self._acc += max(0, n)
                        done = min(max(getattr(self, "initial", 0), 0) + self._acc,
                                   self.total)
                        state["active"][self._key] = done / self.total
                        _emit()
                except Exception:
                    pass
                return r

            def close(self):
                try:
                    if self._key is not None:
                        state["active"].pop(self._key, None)
                except Exception:
                    pass
                return super().close()

        tqdm_class = _OuterTqdm
        if tqmod is not None:
            orig_tqdm = tqmod.tqdm
            tqmod.tqdm = _FileTqdm  # _get_progress_bar_context 用模块全局名
            patched_mod = tqmod
    except Exception:
        pass
    try:
        huggingface_hub.snapshot_download(
            repo_id, cache_dir=cache_dir, tqdm_class=tqdm_class)
    finally:
        if patched_mod is not None:
            patched_mod.tqdm = orig_tqdm
        if progress_cb:
            progress_cb(100)


def hf_snapshot_path(repo_id: str, cache_dir: str) -> str:
    """返回 HF 缓存中完整可加载快照的本地路径；找不到则返回空串。"""
    root = os.path.join(cache_dir, "models--" + repo_id.replace("/", "--"),
                        "snapshots")
    try:
        repo_root = os.path.dirname(root)
        candidates = []
        ref = os.path.join(repo_root, "refs", "main")
        if os.path.isfile(ref):
            with open(ref, "r", encoding="utf-8") as f:
                name = f.read().strip()
            if name:
                candidates.append(os.path.join(root, name))
        candidates.extend(
            os.path.join(root, name) for name in os.listdir(root)
            if os.path.join(root, name) not in candidates)
    except OSError:
        return ""
    model_exts = (".safetensors", ".bin", ".pt", ".onnx", ".gguf")
    for snapshot in candidates:
        if not os.path.isdir(snapshot) or not os.path.isfile(
                os.path.join(snapshot, "config.json")):
            continue
        try:
            if any(name.lower().endswith(model_exts)
                   for _dir, _subdirs, names in os.walk(snapshot)
                   for name in names):
                return snapshot
        except OSError:
            continue
    return ""


def hf_snapshot_ready(repo_id: str, cache_dir: str) -> bool:
    """检查 HF 缓存中是否存在完整的可加载快照，而不是仅检查目录名。"""
    return bool(hf_snapshot_path(repo_id, cache_dir))


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
        self._aborted = False         # 取消标志：阻止后续推理把模型又悄悄拉起来

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
    def download(self, log_cb=None, progress_cb=None):
        """下载模型权重（依赖就绪后）。progress_cb 收 0-100 整数。"""
        cw = self.custom_weights()
        if cw:
            if log_cb:
                log_cb(f"已设置本地权重 {cw}，跳过下载")
            if progress_cb:
                progress_cb(100)
            return
        if self.use_sidecar():
            self._ensure_sidecar(log_cb).download(progress_cb)
            return
        self._download_impl(get_models_dir(), log_cb, progress_cb)

    def load(self, params: dict, log_cb=None, progress_cb=None):
        self._aborted = False  # 新一次加载：解除上一次取消留下的拒绝状态
        params = dict(params or {})
        cw = self.custom_weights()
        if cw:
            params["custom_weights"] = cw  # sidecar worker 无法读设置，随参数传入
        if self.use_sidecar():
            sc = self._ensure_sidecar(log_cb)
            if not sc.is_loaded:
                sc.load(params, progress_cb)
            self._loaded = True
            self._last_params = dict(params or {})
        else:
            self._load_impl(get_models_dir(), params, log_cb, progress_cb)
            self._loaded = True
        if progress_cb:
            progress_cb(100)

    def abort(self):
        """打断在途推理（取消打标 / 关窗用）。sidecar 引擎直接杀进程并唤醒
        等待方；进程内引擎（WD14）无法中断当前这一张。
        置 _aborted 是关键：批量线程后续还会走到 tag_image，若不拦，它会
        发现 sidecar 已死 → 重新拉起进程并重新加载模型（数十秒），用户看到
        的就是"点了取消却还在加载"。"""
        self._aborted = True
        if self._sidecar:
            try:
                self._sidecar.abort()
            except Exception:
                pass
            self._sidecar = None
        self._loaded = False

    def unload(self):
        if self.use_sidecar():
            if self._sidecar:
                self._sidecar.close()
                self._sidecar = None
            self._loaded = False
            return
        self._unload_impl()
        self._loaded = False

    def tag_image(self, path: str, params: dict, progress_cb=None) -> EngineResult:
        if self._aborted:
            raise RuntimeError("已取消")
        if self.use_sidecar():
            sc = self._ensure_sidecar()
            if not sc.is_loaded:
                # sidecar 超时/崩溃后重建的进程是空白的，先恢复上次加载的模型
                sc.load(self._last_params or params)
            r = sc.tag(path, params, progress_cb)
            # sidecar 走 JSON，框被序列化成 dict（worker 里 b.__dict__）；
            # 下游（标签编辑器 b.label / 写 YOLO 框文件）都按 Box 用，这里必须还原。
            boxes = []
            for b in r.get("boxes", []):
                if isinstance(b, Box):
                    boxes.append(b)
                elif isinstance(b, dict):
                    try:
                        boxes.append(Box(**{k: b[k] for k in
                                            ("label", "conf", "x1", "y1", "x2", "y2")
                                            if k in b}))
                    except Exception:
                        pass
            return EngineResult(tags=r.get("tags", []), boxes=boxes)
        r = self._tag_impl(path, params, progress_cb)
        if progress_cb:
            progress_cb(100)
        return r

    # ---------- 进程内实现（sidecar worker / 源码模式调用） ----------
    def _download_impl(self, models_dir: str, log_cb=None, progress_cb=None):
        raise NotImplementedError

    def _load_impl(self, models_dir: str, params: dict, log_cb=None,
                   progress_cb=None):
        raise NotImplementedError

    def _unload_impl(self):
        pass

    def _tag_impl(self, path: str, params: dict, progress_cb=None) -> EngineResult:
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
