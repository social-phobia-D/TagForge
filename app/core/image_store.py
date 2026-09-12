import os
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from app.engines.base import Box  # 检测框类型定义在 engines.base（Qt-free，re-export）

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class ImageItem:
    path: str
    tags: list = field(default_factory=list)  # 全部来源合并视图（只读用途：筛选/补全/导出）
    tags_by_src: dict = field(default_factory=dict)  # 引擎key -> 标签列表（独立文件）
    boxes: list = field(default_factory=list)  # list[Box]

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def has_tags(self) -> bool:
        return bool(self.tags)

    def rebuild_merged(self):
        """由各来源列表重建合并视图（保持来源顺序，去重）"""
        out = []
        for tags in self.tags_by_src.values():
            for t in tags:
                if t not in out:
                    out.append(t)
        self.tags = out


class ImageStore(QObject):
    """图片文件夹数据模型"""

    changed = Signal()
    item_updated = Signal(str)  # path

    def __init__(self):
        super().__init__()
        self.folder = ""
        self.items: list[ImageItem] = []
        self._index: dict[str, ImageItem] = {}  # path -> item，find 从 O(n) 降到 O(1)

    def set_folder(self, folder: str, recursive: bool):
        self.folder = folder
        self.items = []
        self._index = {}
        if recursive:
            for root, _dirs, files in os.walk(folder):
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                        self._add(os.path.join(root, f))
        else:
            for f in sorted(os.listdir(folder)):
                p = os.path.join(folder, f)
                if os.path.isfile(p) and os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                    self._add(p)
        self.changed.emit()

    def _add(self, path: str):
        item = ImageItem(path=path)
        for key in src_keys():
            tags = read_src_tags(path, key)
            if tags:
                item.tags_by_src[key] = tags
        item.rebuild_merged()
        from app.core.tag_writer import read_yolo_boxes  # 延迟导入避免环
        item.boxes = read_yolo_boxes(path)
        self.items.append(item)
        self._index[path] = item

    def find(self, path: str) -> ImageItem | None:
        return self._index.get(path)

    def all_tags(self) -> list:
        """全部图片标签集合（用于自动补全），按出现频率排序"""
        from collections import Counter
        c = Counter()
        for it in self.items:
            c.update(it.tags)
        return [t for t, _ in c.most_common()]

    def tag_frequency(self) -> list:
        from collections import Counter
        c = Counter()
        for it in self.items:
            c.update(it.tags)
        return c.most_common()

    def rewrite_all(self, fn, items=None):
        """对每个图片的每个来源标签列表应用 fn(tags)->tags，
        变化的写回各自来源文件并重建合并视图"""
        for it in (items if items is not None else self.items):
            for key in list(it.tags_by_src):
                new = fn(list(it.tags_by_src[key]))
                if new != it.tags_by_src[key]:
                    it.tags_by_src[key] = new
                    write_src_tags(it.path, key, new)
            it.rebuild_merged()


def _txt_path(img_path: str) -> str:
    return os.path.splitext(img_path)[0] + ".txt"


def src_keys() -> list:
    """全部标签来源 key：各引擎 + main（旧版合并 txt，兼容历史数据）"""
    from app.engines.base import get_engines
    return [e.key for e in get_engines()] + ["main"]


def src_txt_path(img_path: str, key: str) -> str:
    """来源标签文件：<图名>.<来源>.txt；main = 传统 <图名>.txt"""
    stem = os.path.splitext(img_path)[0]
    return f"{stem}.txt" if key == "main" else f"{stem}.{key}.txt"


def read_src_tags(img_path: str, key: str) -> list:
    p = src_txt_path(img_path, key)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        return [t.strip() for t in raw.split(",") if t.strip()]
    except Exception:
        return []


def write_src_tags(img_path: str, key: str, tags: list):
    """原子写入来源标签文件：先写临时文件再 os.replace，避免写一半崩溃"""
    p = src_txt_path(img_path, key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(", ".join(tags))
    os.replace(tmp, p)
