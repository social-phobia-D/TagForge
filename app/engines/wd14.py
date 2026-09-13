import csv
import os

import numpy as np
from PIL import Image

from app.engines.base import EngineBase, EngineResult

REPO = "SmilingWolf/wd-swinv2-tagger-v3"
# selected_tags.csv category: 0=general 1=artist 3=copyright 4=character 9=rating


class Wd14Engine(EngineBase):
    key = "wd14"
    title = "WD14 动漫标签 (Danbooru)"
    description = "SmilingWolf WD-SwinV2 打标器，输出 Danbooru 风格标签，适合动漫/二次元图。依赖随主程序内置。"
    vram_note = "CPU 即可运行，速度约 0.3-1 秒/张"
    pip_deps = []  # onnxruntime/huggingface_hub 随主程序内置
    weights_size = "约 450 MB"
    need_torch = False
    weight_urls = [f"https://hf-mirror.com/{REPO}", f"https://huggingface.co/{REPO}"]
    custom_hint = "选包含 model.onnx 和 selected_tags.csv 的文件夹"

    def _model_dir(self) -> str:
        return os.path.join(self.models_dir(), "wd14")

    def weights_ready(self) -> bool:
        cw = self.custom_weights()
        if cw:
            return os.path.isfile(os.path.join(cw, "model.onnx"))
        d = self._model_dir()
        return os.path.exists(os.path.join(d, "model.onnx")) and os.path.exists(
            os.path.join(d, "selected_tags.csv"))

    def _download_impl(self, models_dir: str, log_cb=None, progress_cb=None):
        import huggingface_hub
        d = self._model_dir()
        os.makedirs(d, exist_ok=True)
        files = ("model.onnx", "selected_tags.csv")
        for i, fn in enumerate(files):
            if log_cb:
                log_cb(f"WD14: 下载 {REPO}/{fn} ...")
            if progress_cb:
                progress_cb(int(i * 100 / len(files)))
            # hf_hub_download 不接受进度回调，只能按文件给里程碑百分比；
            # 单文件内部进度由库自己打到 stderr（sidecar 日志）。
            huggingface_hub.hf_hub_download(REPO, fn, local_dir=d)
            if progress_cb:
                progress_cb(int((i + 1) * 100 / len(files)))

    def _load_impl(self, models_dir: str, params: dict, log_cb=None,
                   progress_cb=None):
        if progress_cb:
            progress_cb(5)
        if log_cb:
            log_cb("WD14: 导入 onnxruntime ...")
        import onnxruntime as ort
        if progress_cb:
            progress_cb(40)
        d = (params or {}).get("custom_weights") \
            or self.custom_weights() or self._model_dir()
        providers = ["CPUExecutionProvider"]
        try:
            avail = ort.get_available_providers()
            if "CUDAExecutionProvider" in avail:
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        except Exception:
            pass
        if log_cb:
            log_cb(f"WD14: 使用 {'CUDA' if 'CUDA' in providers[0] else 'CPU'} 推理")
        self.session = ort.InferenceSession(os.path.join(d, "model.onnx"), providers=providers)
        if progress_cb:
            progress_cb(85)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        # NHWC: [batch, H, W, 3] -> target 取 H
        self.target = inp.shape[1] if isinstance(inp.shape[1], int) and inp.shape[1] > 32 else 448
        with open(os.path.join(d, "selected_tags.csv"), "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.tag_names = [r["name"] for r in rows]
        self.categories = [int(r["category"]) for r in rows]

    def _unload_impl(self):
        self.session = None

    def _preprocess(self, path: str) -> np.ndarray:
        target = self.target
        with Image.open(path) as pil:
            pil = pil.convert("RGBA")
            bg = Image.new("RGBA", pil.size, (255, 255, 255, 255))
            pil = Image.alpha_composite(bg, pil).convert("RGB")
            arr = np.asarray(pil)[:, :, ::-1]  # RGB -> BGR
        pil = Image.fromarray(arr).resize((target, target))
        arr = np.asarray(pil, dtype=np.float32)
        return arr

    def _tag_impl(self, path: str, params: dict, progress_cb=None) -> EngineResult:
        t_general = float(params.get("general_threshold", 0.35))
        t_char = float(params.get("char_threshold", 0.85))
        underscores = bool(params.get("underscores", False))

        arr = self._preprocess(path)
        preds = self.session.run(None, {self.input_name: arr[None, ...]})[0][0]

        def fmt(name: str) -> str:
            if not underscores:
                name = name.replace("_", " ")
            return name.replace("(", r"\(").replace(")", r"\)")

        tags = []
        # rating: 取最高
        rating_idx = [i for i, c in enumerate(self.categories) if c == 9]
        if rating_idx:
            best = max(rating_idx, key=lambda i: preds[i])
            if preds[best] >= 0.25:
                tags.append(fmt(self.tag_names[best]))
        # 其余按分数降序
        scored = [(float(preds[i]), i) for i, c in enumerate(self.categories)
                  if c == 0 and preds[i] >= t_general]
        scored += [(float(preds[i]), i) for i, c in enumerate(self.categories)
                   if c == 4 and preds[i] >= t_char]
        scored.sort(reverse=True)
        tags += [fmt(self.tag_names[i]) for _s, i in scored]
        return EngineResult(tags=tags)
