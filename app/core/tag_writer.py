"""标签导出：CSV / YOLO 检测框"""
import json
import os

from PIL import Image

from app.core.image_store import ImageItem, _artifact_stem


def export_csv(items: list, out_path: str):
    import csv
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["文件名", "路径", "标签"])
        for it in items:
            tags = list(it.tags)
            for box in it.boxes:
                label = str(getattr(box, "label", "")).strip()
                if label and label not in tags:
                    tags.append(label)
            w.writerow([it.name, it.path, ", ".join(tags)])


def _labels_paths(img_path: str):
    labels_dir = os.path.join(os.path.dirname(img_path), "labels")
    stem = _artifact_stem(img_path)
    return (labels_dir, os.path.join(labels_dir, stem + ".txt"),
            os.path.join(labels_dir, stem + ".sources.json"),
            os.path.join(labels_dir, "classes.txt"))


def _legacy_labels_paths(img_path: str):
    """旧版同名图片共用的 YOLO 文件路径，仅用于兼容读取。"""
    labels_dir = os.path.join(os.path.dirname(img_path), "labels")
    stem = os.path.splitext(os.path.basename(img_path))[0]
    return (os.path.join(labels_dir, stem + ".txt"),
            os.path.join(labels_dir, "classes.txt"))


def _box_to_source_row(box, w: int, h: int) -> dict:
    x1, y1, x2, y2 = _pixel_box(box, w, h)
    return {
        "label": str(getattr(box, "label", "")),
        "conf": float(getattr(box, "conf", 1.0)),
        "x1": x1 / w,
        "y1": y1 / h,
        "x2": x2 / w,
        "y2": y2 / h,
    }


def _pixel_box(box, w: int, h: int):
    """排序并裁剪到图像边界，避免无效 YOLO 坐标污染数据集。"""
    x1, x2 = sorted((float(box.x1), float(box.x2)))
    y1, y2 = sorted((float(box.y1), float(box.y2)))
    return (max(0.0, min(float(w), x1)),
            max(0.0, min(float(h), y1)),
            max(0.0, min(float(w), x2)),
            max(0.0, min(float(h), y2)))


def write_yolo_labels(img_path: str, boxes: list, boxes_by_src: dict = None) -> str:
    """写 YOLO 总文件，并保存框来源元数据。

    总文件仍是标准 ``labels/<stem>.txt``，来源元数据写在同名
    ``.sources.json``，用于下次启动时区分手工框和各引擎框，避免重新打标
    覆盖手工标注或叠加引擎互相覆盖。
    """
    labels_dir, out, sources_path, classes_path = _labels_paths(img_path)
    os.makedirs(labels_dir, exist_ok=True)
    classes = []
    if os.path.exists(classes_path):
        with open(classes_path, "r", encoding="utf-8") as f:
            classes = [l.strip() for l in f if l.strip()]

    with Image.open(img_path) as im:
        w, h = im.size

    lines = []
    for b in boxes:
        if not str(getattr(b, "label", "")).strip():
            continue
        if b.label not in classes:
            classes.append(b.label)
        idx = classes.index(b.label)
        x1, y1, x2, y2 = _pixel_box(b, w, h)
        cx = ((x1 + x2) / 2) / w
        cy = ((y1 + y2) / 2) / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        lines.append(f"{idx} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

    # 原子写入：先写 .tmp 再 replace，防止崩溃留下半截文件
    for target, content in ((out, "\n".join(lines)),
                            (classes_path, "\n".join(classes))):
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, target)

    if boxes_by_src is None:
        boxes_by_src = {"manual": list(boxes)}
    source_rows = {
        str(source): [_box_to_source_row(b, w, h) for b in source_boxes
                      if str(getattr(b, "label", "")).strip()]
        for source, source_boxes in boxes_by_src.items()
    }
    tmp = sources_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "sources": source_rows}, f,
                  ensure_ascii=False, indent=2)
    os.replace(tmp, sources_path)
    return out


def read_yolo_boxes(img_path: str) -> list:
    """读 <图片目录>/labels/<stem>.txt，还原为像素坐标 Box 列表"""
    from app.core.image_store import Box

    _labels_dir, p, _sources_path, classes_path = _labels_paths(img_path)
    if not os.path.exists(p) and _artifact_stem(img_path) != \
            os.path.splitext(os.path.basename(img_path))[0]:
        legacy_p, legacy_classes_path = _legacy_labels_paths(img_path)
        if os.path.exists(legacy_p):
            p, classes_path = legacy_p, legacy_classes_path
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
            if len(r) < 5:
                continue
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


def read_yolo_boxes_by_src(img_path: str) -> dict:
    """读取来源化框；旧版只有标准 YOLO 文件时归入 manual。"""
    from app.core.image_store import Box

    _labels_dir, _p, sources_path, _classes_path = _labels_paths(img_path)
    if not os.path.exists(sources_path):
        legacy = read_yolo_boxes(img_path)
        return {"manual": legacy} if legacy else {}
    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        sources = payload.get("sources")
        if not isinstance(sources, dict):
            raise ValueError("invalid box source metadata")
        with Image.open(img_path) as im:
            w, h = im.size
        result = {}
        for source, rows in sources.items():
            if not isinstance(rows, list):
                raise ValueError("invalid box source rows")
            boxes = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("label", "")).strip()
                if not label:
                    continue
                boxes.append(Box(
                    label=label, conf=float(row.get("conf", 1.0)),
                    x1=float(row["x1"]) * w, y1=float(row["y1"]) * h,
                    x2=float(row["x2"]) * w, y2=float(row["y2"]) * h))
            result[str(source)] = boxes
        return result
    except Exception:
        legacy = read_yolo_boxes(img_path)
        return {"manual": legacy} if legacy else {}
