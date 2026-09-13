"""自检：缩略图紧凑模式行宽 + 画布滚轮缩放。离屏跑：
QT_QPA_PLATFORM=offscreen python -u test_zoom_thumb.py"""
import faulthandler
import os
import sys

faulthandler.enable()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem

app = QApplication(sys.argv)

# ---- 准备：临时测试图 ----
from PySide6.QtGui import QImage

os.makedirs("test_images", exist_ok=True)
IMG = os.path.abspath("test_images/_zoomtest.png")
img = QImage(200, 150, QImage.Format_RGB32)
img.fill(0xFF8844AA)
assert img.save(IMG)

# ================= 1. 画布滚轮缩放 =================
from app.core.image_store import ImageItem
from app.ui.box_canvas import BoxCanvas

c = BoxCanvas()
c.resize(400, 300)
it = ImageItem(path=IMG)
c.set_item(it)
app.processEvents()

fit = min(400 / 200, 300 / 150)
ox, oy, s = c._map_geo()
assert abs(s - fit) < 1e-9, f"初始应适配窗口, s={s}"


def wheel(cx, cy, dy):
    ev = QWheelEvent(QPointF(cx, cy), QPointF(cx, cy), QPoint(0, 0),
                     QPoint(0, dy), Qt.NoButton, Qt.NoModifier,
                     Qt.ScrollUpdate, False)
    c.wheelEvent(ev)


# 放大：光标 (350,200) 为锚点
ix_before = (350 - ox) / s
iy_before = (200 - oy) / s
wheel(350, 200, 120)
ox2, oy2, s2 = c._map_geo()
assert s2 > s, "放大后 s 应变大"
assert abs((350 - ox2) / s2 - ix_before) < 0.01, "水平锚点应保持"
assert abs((200 - oy2) / s2 - iy_before) < 0.01, "垂直锚点应保持"
assert ox2 <= 0 and oy2 <= 0, "偏移不应把图拖出左/上边界"
assert ox2 >= 400 - 200 * s2 - 1e-6, "不应拖出右边界"
assert c._off is not None

# 缩回：反复滚小直到复位
for _ in range(30):
    wheel(350, 200, -120)
    if c._off is None:
        break
assert c._off is None, "缩回 1 倍应复位居中"
_, _, s3 = c._map_geo()
assert abs(s3 - fit) < 1e-6, f"复位后应回到 fit, s={s3}"

# 换图自动复位
wheel(100, 100, 120)
assert c._zoom > 1.0
c.set_item(it)
assert c._zoom == 1.0 and c._off is None

# 锁定时滚轮无效
c._zoom = 1.5
c._off = QPointF(-10, -10)
c.locked = True
wheel(100, 100, 120)
assert c._zoom == 1.5, "锁定时滚轮不应缩放"
c.locked = False

# ================= 2. 缩略图紧凑模式行宽 =================
from app.core.settings import AppSettings
from app.ui.main_window import MainWindow

st = AppSettings()
old_dir = st.get_last_dir()
st.set_last_dir("")  # 避免构造时加载真实目录/启动缩略图线程
QSettings("Dabiao", "dabiao").setValue("general/language", "zh")

mw = MainWindow()
NAME = "a_very_long_file_name_that_needs_elide_1234567890.png"
li = QListWidgetItem(NAME)
li.setData(Qt.UserRole, "x")
mw.thumb_list.addItem(li)

mw.show()
app.processEvents()
mw.thumb_list.resize(130, 500)
app.processEvents()
assert mw._thumb_compact is True, "视口 <190 应进入紧凑模式"
assert mw.thumb_list.viewMode() == QListWidget.ViewMode.ListMode
mw._update_compact_hints()
r = mw.thumb_list.visualItemRect(li)
print(f"compact rect={r.width():.0f}x{r.height():.0f}")
assert 100 <= r.width() <= 300, f"紧凑行宽异常: {r.width()}"
assert r.height() == 22

mw._thumb_compact = False  # 直接验证图标模式分支（默认布局已验证过的路径）
mw._apply_thumb_mode()
r2 = mw.thumb_list.visualItemRect(li)
print(f"icon rect={r2.width():.0f}x{r2.height():.0f}")
assert r2.width() == 180 and r2.height() == 200

# ================= 3. 手动框选来源 + 框可见性 =================
from app.core.image_store import Box

item2 = ImageItem(path=IMG)
item2.boxes = [Box(label="dog", x1=1, y1=1, x2=10, y2=10),
               Box(label="dog", x1=5, y1=5, x2=20, y2=20),
               Box(label="person", x1=2, y1=2, x2=30, y2=30)]
mw.editor.set_item(item2)
labels = [mw.editor.list.item(i).text()
          for i in range(mw.editor.list.count())]
assert labels == ["dog", "dog", "person"], labels  # 每行=一个框
assert mw.editor.btn_del.isEnabled(), "手动来源删除应可用"
assert not mw.editor.btn_add.isEnabled(), "手动来源添加应禁用"

# 改名 → 同步到框并落盘
mw.editor.list.item(0).setText("cat")
assert item2.boxes[0].label == "cat", "改名应同步到框"
# 删除选中行 → 删除对应框
mw.editor.list.setCurrentRow(1)
mw.editor._remove_selected()
assert len(item2.boxes) == 2 and item2.boxes[0].label == "cat"
assert mw.editor.list.count() == 2

mw.editor.src_filter.setCurrentIndex(1)  # wd14：无框来源
assert mw.preview._boxes_visible is False, "WD14 来源不应显示框"
assert mw.editor.btn_add.isEnabled()
mw.editor.src_filter.setCurrentIndex(3)  # yoloworld：有框来源
assert mw.preview._boxes_visible is True
mw.editor.src_filter.setCurrentIndex(0)  # 手动框选
assert mw.preview._boxes_visible is True

# 编辑器选中 → 画布框高亮
mw.preview.set_item(item2)
mw.editor.list.setCurrentRow(1)  # 手动来源行1 = person
assert mw.preview._sel == 1
item2.tags_by_src["yoloworld"] = ["person"]
mw.editor.src_filter.setCurrentIndex(3)  # yoloworld：按标签名匹配
mw.editor.list.setCurrentRow(0)
assert mw.preview._sel == 1, "按名匹配应选中 person 框"

# 框隐藏时：命中禁用，滚轮缩放仍可用
mw.preview.set_item(item2)
mw.preview.set_boxes_visible(False)
assert mw.preview._hit_box(50, 50) == -1
z0 = mw.preview._zoom
mw.preview.wheelEvent(QWheelEvent(QPointF(100, 100), QPointF(100, 100),
                                  QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                                  Qt.NoModifier, Qt.ScrollUpdate, False))
assert mw.preview._zoom > z0, "框隐藏时滚轮缩放应仍可用"

# ================= 4. 自定义权重路径 =================
from app.engines.base import get_engine

q = QSettings("Dabiao", "dabiao")
old_wd14_w = q.value("weights/wd14", "")
old_yolo_w = q.value("weights/yoloworld", "")

os.makedirs("test_images/wd14fake", exist_ok=True)
open("test_images/wd14fake/model.onnx", "wb").close()
open("test_images/wd14fake/selected_tags.csv", "wb").close()

q.setValue("weights/wd14", os.path.abspath("test_images/wd14fake"))
q.setValue("weights/yoloworld", IMG)  # 任意存在的文件
we = get_engine("wd14")
ye = get_engine("yoloworld")
assert we.weights_ready() is True, "wd14 自定义目录应判就绪"
assert ye.weights_ready() is True, "yolo 自定义文件应判就绪"
assert we.weight_urls and ye.weight_urls, "引擎应提供手动下载地址"

q.setValue("weights/wd14", "")
q.setValue("weights/yoloworld", "")
assert we.custom_weights() == "" and ye.custom_weights() == "", "清除应生效"

# ---- 5. 取消能打断在途推理（曾经点了取消没反应）----
from app.core.batch_worker import BatchWorker


class _FakeEngine:
    """记录 abort 是否被调用；tag_image 模拟一次"永不返回"的推理"""
    key = "fake"
    title = "假引擎"

    def __init__(self):
        self.aborted = False

    def abort(self):
        self.aborted = True

    def tag_image(self, path, params, progress_cb=None):
        raise RuntimeError("已取消")


fe = _FakeEngine()
bw = BatchWorker(None, [fe], ["x.jpg"], {}, "replace", "", "")
bw.stop()
assert fe.aborted is True, "取消必须 abort 引擎（否则要等在途请求跑完/超时）"

# 引擎无 sidecar 时 abort 不应炸，且要复位加载状态
ye._loaded = True
ye.abort()
assert ye._loaded is False, "abort 后应视为未加载"

# ---- 6. 取消后不再偷偷重启 sidecar / 重新加载模型（_aborted 闸门）----
from app.engines.base import EngineBase, EngineResult


class _ProbeEngine(EngineBase):
    key = "probe"
    title = "探针引擎"

    def __init__(self):
        super().__init__()
        self.tagged = 0

    def weights_ready(self):
        return True

    def _download_impl(self, models_dir, log_cb=None, progress_cb=None):
        pass

    def _load_impl(self, models_dir, params, log_cb=None, progress_cb=None):
        if progress_cb:
            progress_cb(100)

    def _tag_impl(self, path, params, progress_cb=None):
        self.tagged += 1
        return EngineResult(tags=["x"])


pe = _ProbeEngine()
pe.load({})
assert pe.tagged == 0
pe.tag_image("a.jpg", {})
assert pe.tagged == 1, "正常状态下应能推理"
# 取消中：tag_image 必须立刻抛错，绝不能再走 _ensure_sidecar()→重新加载模型
pe.abort()
try:
    pe.tag_image("b.jpg", {})
    raise AssertionError("abort 后 tag_image 应立刻失败")
except RuntimeError as e:
    assert "取消" in str(e), e
assert pe.tagged == 1, "取消后不应再执行推理"
# 重新加载（=下一次打标）后必须解除闸门
pe.load({})
pe.tag_image("c.jpg", {})
assert pe.tagged == 2, "load() 应清除 _aborted"

# 批量：取消后不得进入下一个引擎（否则它会重新拉起 sidecar 并重新加载）
from threading import Event


class _SlowEngine:
    """第一张图卡住直到被 abort；第二个引擎必须完全不被调用"""

    key = "slow"
    title = "慢引擎"

    def __init__(self):
        self.entered = Event()   # 已进入推理（测试线程据此触发取消）
        self.release = Event()   # abort() 后放行在途推理
        self.aborted = False
        self.calls = 0

    def abort(self):
        self.aborted = True
        self.release.set()

    def tag_image(self, path, params, progress_cb=None):
        self.calls += 1
        self.entered.set()
        self.release.wait(10)     # 模拟卡在途中的长推理
        raise RuntimeError("已取消")


class _NeverEngine:
    key = "never"
    title = "不该被调用的引擎"
    calls = 0

    def abort(self):
        pass

    def tag_image(self, path, params, progress_cb=None):
        type(self).calls += 1
        raise AssertionError("取消后不应再调用下一个引擎")


slow, never = _SlowEngine(), _NeverEngine()


class _Item:
    def __init__(self):
        self.path = "x.jpg"
        self.tags = []
        self.boxes = []
        self.tags_by_src = {}

    def rebuild_merged(self):
        pass


class _Store:
    def find(self, path):
        return _Item()       # 两个引擎都会抛错，_merge 不会被执行


bw2 = BatchWorker(None, [slow, never], ["x.jpg"], {}, "replace", "", "")
bw2.store = _Store()


def _cancel_when_inside():
    assert slow.entered.wait(5), "第一个引擎没有被调用"
    bw2.stop()


from threading import Thread
tc = Thread(target=_cancel_when_inside)
tc.start()
bw2.run()          # 直接同步跑（不起线程，便于断言）
tc.join()
assert slow.calls == 1, slow.calls
assert slow.aborted is True
assert _NeverEngine.calls == 0, "取消后必须停止，不能再进入下一个引擎"

# ---- 7. 下载进度回调（字节级，断网也能验）----
from app.engines.sidecar import _download

os.makedirs("test_images", exist_ok=True)
SRC = os.path.abspath("test_images/_prog_src.bin")
DST = os.path.abspath("test_images/_prog_dst.bin")
with open(SRC, "wb") as f:
    f.write(b"\0" * (3 << 20))


class _Srv:
    """本地 HTTP 服务：模拟一个带 Content-Length 的权重下载"""
    def __init__(self, data):
        self.data = data
        import http.server
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                rng = self.headers.get("Range")
                start = 0
                if rng and rng.startswith("bytes="):
                    start = int(rng.split("=")[1].split("-")[0])
                body = outer.data[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(outer.data)-1}/{len(outer.data)}")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/w.bin"

    def __exit__(self, *a):
        self.httpd.shutdown()


with open(SRC, "rb") as f:
    payload = f.read()
with _Srv(payload) as url:
    pcts = []
    _download(url, DST, None, timeout=10, progress_cb=pcts.append)
assert os.path.getsize(DST) == len(payload), "下载内容应完整"
assert pcts[-1] == 100, pcts[-3:]
assert pcts == sorted(pcts), "进度不可回退"
assert len(pcts) > 1, "应上报多次进度"

# hf_snapshot_download：外层按文件 + 内层按字节的合成进度（离线模拟）
import huggingface_hub
from huggingface_hub.utils.tqdm import _get_progress_bar_context
from tqdm.contrib.concurrent import thread_map

_FAKE = [("big.safetensors", 600), ("big2.safetensors", 300),
         ("config.json", 1), ("tokenizer.json", 99)]
_SIZE = dict(_FAKE)
_real_snapshot = huggingface_hub.snapshot_download


def _fake_snapshot(repo_id, *, cache_dir=None, tqdm_class=None, **kw):
    import time

    def one(name):
        with _get_progress_bar_context(desc=name, log_level=40,
                                       total=_SIZE[name], initial=0,
                                       unit="B") as pbar:
            for _ in range(_SIZE[name]):
                time.sleep(0.0002)   # 真实下载不是瞬间完成的
                pbar.update(1)
        return name
    list(thread_map(one, [f for f, _ in _FAKE], desc="F", max_workers=4,
                    tqdm_class=tqdm_class))


huggingface_hub.snapshot_download = _fake_snapshot
try:
    from app.engines.base import hf_snapshot_download
    hp = []
    hf_snapshot_download("fake/repo", "test_images/_hf", None, hp.append)
finally:
    huggingface_hub.snapshot_download = _real_snapshot
assert hp[-1] == 100, hp[-3:]
assert hp == sorted(hp), f"HF 进度不可回退: {hp}"
assert len(hp) > 8, f"HF 进度粒度太粗（内层字节进度没生效）: {hp}"

# ---- 清理 ----
st.set_last_dir(old_dir)
os.remove(IMG)
import shutil
shutil.rmtree("test_images/labels", ignore_errors=True)
shutil.rmtree("test_images/wd14fake", ignore_errors=True)
shutil.rmtree("test_images/_hf", ignore_errors=True)
for _f in (SRC, DST):
    try:
        os.remove(_f)
    except OSError:
        pass
q.setValue("weights/wd14", old_wd14_w)
q.setValue("weights/yoloworld", old_yolo_w)
print("ALL OK")
os._exit(0)
