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

# ---- 清理 ----
st.set_last_dir(old_dir)
os.remove(IMG)
import shutil
shutil.rmtree("test_images/labels", ignore_errors=True)
shutil.rmtree("test_images/wd14fake", ignore_errors=True)
q.setValue("weights/wd14", old_wd14_w)
q.setValue("weights/yoloworld", old_yolo_w)
print("ALL OK")
os._exit(0)
