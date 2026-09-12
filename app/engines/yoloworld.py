import os

from app.engines.base import Box, EngineBase, EngineResult, get_data_root

WEIGHTS_NAME = "yolov8s-worldv2.pt"

# ultralytics 的配置/字体/CLIP 权重也必须放数据盘，不许写到 C 盘用户目录
os.environ.setdefault("YOLO_CONFIG_DIR",
                      os.path.join(get_data_root(), "ultralytics"))
os.makedirs(os.environ["YOLO_CONFIG_DIR"], exist_ok=True)


class YoloWorldEngine(EngineBase):
    key = "yoloworld"
    title = "YOLO-World 极速检测 (腾讯)"
    description = "毫秒级开放词表检测，批量打标最快。需在下方填写候选类名（英文），模型只找这些类别。"
    vram_note = "约 1-2GB 显存，CPU 也可实时"
    pip_deps = ["ultralytics", "open-clip-torch"]
    import_deps = ["ultralytics", "open_clip"]
    weights_size = "约 25 MB"
    need_torch = True
    has_boxes = True
    weight_urls = [
        # 国内可达的官方权重镜像（Ultralytics 官方 HF 仓库无 world 系列）
        "https://hf-mirror.com/Bingsu/yolo-world-mirror/resolve/main/yolov8s-worldv2.pt",
        "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s-worldv2.pt",
    ]
    custom_hint = "选 yolov8s-worldv2.pt 权重文件"
    custom_is_file = True

    def _weights_path(self) -> str:
        return os.path.join(self.models_dir(), WEIGHTS_NAME)

    def weights_ready(self) -> bool:
        cw = self.custom_weights()
        if cw:
            return os.path.isfile(cw)
        return os.path.exists(self._weights_path())

    def _download_impl(self, models_dir: str, log_cb=None):
        if log_cb:
            log_cb("YOLO-World: 下载 yolov8s-worldv2.pt ...")
        from app.engines.sidecar import download_file  # 带超时的分块下载
        urls = [
            # 国内可达的官方权重镜像（Ultralytics 官方 HF 仓库无 world 系列）
            "https://hf-mirror.com/Bingsu/yolo-world-mirror/resolve/main/yolov8s-worldv2.pt",
            "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s-worldv2.pt",
        ]
        last_err = None
        for url in urls:
            try:
                download_file(url, self._weights_path(), log_cb, timeout=120)
                break
            except Exception as e:
                last_err = e
        else:
            raise RuntimeError(f"YOLO-World 权重下载失败: {last_err}")
        if log_cb:
            log_cb("YOLO-World: 下载完成")

    def _load_impl(self, models_dir: str, params: dict, log_cb=None):
        if log_cb:
            log_cb("YOLO-World: 导入 ultralytics（首次约 5 秒）...")
        from ultralytics import YOLO
        wpt = (params or {}).get("custom_weights") \
            or self.custom_weights() or self._weights_path()
        self.model = YOLO(wpt)
        try:
            import torch
            self.device = 0 if torch.cuda.is_available() else "cpu"
        except Exception:
            self.device = "cpu"
        self._last_classes = None
        if log_cb:
            log_cb(f"YOLO-World: 就绪 ({'GPU' if self.device == 0 else 'CPU'})")

    def _unload_impl(self):
        self.model = None

    def _tag_impl(self, path: str, params: dict) -> EngineResult:
        result = EngineResult()
        vocab = [c.strip() for c in str(params.get("classes", "")).split(",") if c.strip()]
        if not vocab:
            return result
        if vocab != self._last_classes:
            self.model.set_classes(vocab)
            self._last_classes = vocab
        r = self.model.predict(path, conf=float(params.get("conf", 0.25)),
                               device=self.device, verbose=False)[0]
        seen = set()
        for xyxy, cls, conf in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(),
                                   r.boxes.conf.tolist()):
            name = r.names[int(cls)]
            if name not in seen:
                seen.add(name)
                result.tags.append(name)
            result.boxes.append(Box(
                label=name, conf=float(conf),
                x1=xyxy[0], y1=xyxy[1], x2=xyxy[2], y2=xyxy[3]))
        return result
