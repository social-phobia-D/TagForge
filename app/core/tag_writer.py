"""标签导出：CSV / YOLO 检测框"""
import os

from PIL import Image

from app.core.image_store import ImageItem


def export_csv(items: list, out_path: str):
    import csv
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["文件名", "路径", "标签"])
        for it in items:
            w.writerow([it.name, it.path, ", ".join(it.tags)])


def write_yolo_labels(img_path: str, boxes: list) -> str:
    """写 <图片目录>/labels/<stem>.txt（YOLO 归一化 cx cy w h）并维护 classes.txt"""
    labels_dir = os.path.join(os.path.dirname(img_path), "labels")
    os.makedirs(labels_dir, exist_ok=True)
    classes_path = os.path.join(labels_dir, "classes.txt")
    classes = []
    if os.path.exists(classes_path):
        with open(classes_path, "r", encoding="utf-8") as f:
            classes = [l.strip() for l in f if l.strip()]

    with Image.open(img_path) as im:
        w, h = im.size

    lines = []
    for b in boxes:
        if b.label not in classes:
            classes.append(b.label)
        idx = classes.index(b.label)
        cx = ((b.x1 + b.x2) / 2) / w
        cy = ((b.y1 + b.y2) / 2) / h
        bw = abs(b.x2 - b.x1) / w
        bh = abs(b.y2 - b.y1) / h
        lines.append(f"{idx} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

    stem = os.path.splitext(os.path.basename(img_path))[0]
    out = os.path.join(labels_dir, stem + ".txt")
    # 原子写入：先写 .tmp 再 replace，防止崩溃留下半截文件
    for target, content in ((out, "\n".join(lines)),
                            (classes_path, "\n".join(classes))):
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, target)
    return out


def read_yolo_boxes(img_path: str) -> list:
    """读 <图片目录>/labels/<stem>.txt，还原为像素坐标 Box 列表"""
    from app.core.image_store import Box

    labels_dir = os.path.join(os.path.dirname(img_path), "labels")
    stem = os.path.splitext(os.path.basename(img_path))[0]
    p = os.path.join(labels_dir, stem + ".txt")
    classes_path = os.path.join(labels_dir, "classes.txt")
    if not (os.path.exists(p) and os.path.exists(classes_path)):
        return []
    try:
        with open(classes_path, "r", encoding="utf-8") as f:
            classes = [l.strip() for l in f if l.strip()]
        with open(p, "r", encoding="utf-8") as f:
            rows = [l.split() for l in f if l.strip()]
        if not rows:
            return []
        with Image.open(img_path) as im:
            w, h = im.size
        boxes = []
        for r in rows:
            idx = int(r[0])
            cx, cy, bw, bh = (float(v) for v in r[1:5])
            label = classes[idx] if 0 <= idx < len(classes) else f"class{idx}"
            boxes.append(Box(
                label=label, conf=1.0,
                x1=(cx - bw / 2) * w, y1=(cy - bh / 2) * h,
                x2=(cx + bw / 2) * w, y2=(cy + bh / 2) * h))
        return boxes
    except Exception:
        return []
