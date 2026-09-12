"""缩略图磁盘缓存。

万张级图库下，每次打开文件夹都对原图做全量解码 + 缩放非常慢；
把 168px 缩略图按 (路径, mtime, 大小, 尺寸) 缓存到数据目录，
重开文件夹时直接解码已缩放的小图，速度提升一个数量级。
缓存键含 mtime 与文件大小，原图修改后自动失效。容量超限时按最旧清理。
"""
import hashlib
import os

from PySide6.QtGui import QImage

# 缓存总容量上限（字节），超过后清理最旧的文件
MAX_CACHE_BYTES = 500 * 1024 * 1024

_DIR = None


def cache_dir() -> str:
    global _DIR
    if _DIR is None:
        from app.engines.base import get_data_root  # 延迟导入避免环
        _DIR = os.path.join(get_data_root(), "thumb_cache")
        os.makedirs(_DIR, exist_ok=True)
    return _DIR


def _cache_path(src: str, size: int) -> str:
    try:
        st = os.stat(src)
        key_src = f"{src}|{st.st_mtime_ns}|{st.st_size}|{size}"
    except OSError:
        return ""
    key = hashlib.md5(key_src.encode("utf-8", "surrogatepass")).hexdigest()
    return os.path.join(cache_dir(), key + ".jpg")


def cached_thumb(src: str, size: int) -> QImage:
    """命中返回已缩放的缩略图，未命中返回空 QImage。"""
    p = _cache_path(src, size)
    if p and os.path.exists(p):
        img = QImage(p)
        if not img.isNull():
            return img
    return QImage()


def store_thumb(src: str, size: int, image: QImage):
    """把缩放后的缩略图写入缓存；失败静默（缓存只是加速，不该影响主流程）。"""
    try:
        p = _cache_path(src, size)
        if not p:
            return
        image.save(p, "JPG", 85)
        _prune_if_needed()
    except Exception:
        pass


def _prune_if_needed():
    """超过容量上限时按修改时间从旧到新清理。"""
    try:
        d = cache_dir()
        entries = []
        total = 0
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
                entries.append((st.st_mtime, st.st_size, p))
                total += st.st_size
            except OSError:
                continue
        if total <= MAX_CACHE_BYTES:
            return
        entries.sort()
        for _mtime, fsize, p in entries:
            if total <= MAX_CACHE_BYTES * 0.8:  # 清到 80%，避免频繁触发
                break
            try:
                os.remove(p)
                total -= fsize
            except OSError:
                continue
    except Exception:
        pass
