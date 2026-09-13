"""sidecar 引擎 worker：由独立 Python 运行时启动，stdin/stdout JSON-lines 协议。

用法: python sidecar_worker.py <engine_key>
"""
import json
import os
import sys
import threading


def main():
    if len(sys.argv) < 2:
        return
    engine_key = sys.argv[1]
    worker_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(worker_dir) if os.path.basename(worker_dir) == "engines" \
        else worker_dir
    # app 包的父目录才需要进 sys.path（dev=项目根，frozen=_MEIPASS）。
    # 必须 append 而非 insert(0)：frozen 时 _MEIPASS 里有主程序打包的
    # PySide6/numpy 等（版本对应打包 Python），插到最前会遮蔽运行时
    # 自装的依赖并触发 DLL 版本冲突
    pkg_parent = os.path.dirname(root)
    if pkg_parent not in sys.path:
        sys.path.append(pkg_parent)

    # 协议通道隔离：ultralytics/tqdm/transformers 都会往 stdout 打印，一旦与
    # JSON 回复拼进同一行，父进程就解析不到回复 → 干等满超时（表现为卡死）。
    # 协议只走这个原始句柄，其余 print 一律改道 stderr（落到 sidecar-*.log）。
    proto = sys.stdout
    sys.stdout = sys.stderr

    # 进度回调是从下载线程（huggingface_hub 的线程池 / 我们的字节条）里打的，
    # 多个线程同时 write 会把两行 JSON 交错拼在一起 → 父进程解析不到 reply，
    # 只能干等满超时（又是"卡死"）。写协议必须串行。
    _send_lock = threading.Lock()

    def send(obj):
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with _send_lock:
            proto.write(line)
            proto.flush()

    def log(msg):
        send({"type": "log", "msg": msg})

    def prog(value):
        """进度上报（0-100）。父进程按 type=progress 分发给 UI 进度条"""
        try:
            send({"type": "progress", "value": int(value)})
        except Exception:
            pass

    try:
        from app.engines.base import get_engine
        engine = get_engine(engine_key)
    except Exception as e:
        send({"type": "error", "msg": f"加载引擎模块失败: {e}"})
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        op = req.get("op")
        try:
            if op == "download":
                engine._download_impl(req.get("models_dir"), log, prog)
                send({"type": "done"})
            elif op == "load":
                engine._load_impl(req.get("models_dir"), req.get("params") or {},
                                  log, prog)
                send({"type": "loaded", "ok": True, "msg": "ok"})
            elif op == "tag":
                r = engine._tag_impl(req["path"], req.get("params") or {}, prog)
                boxes = [b.__dict__ for b in r.boxes]
                send({"type": "result", "data": {"tags": r.tags, "boxes": boxes}})
            elif op == "exit":
                send({"type": "done"})
                break
        except Exception as e:
            send({"type": "error", "msg": f"{op} 失败: {e}"})


if __name__ == "__main__":
    main()
