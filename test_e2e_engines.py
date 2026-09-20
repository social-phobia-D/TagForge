"""端到端验证：走真实 sidecar 子进程协议（仿真 frozen 打包路径），
确认 (1) 引擎能加载/推理，(2) 回来的框被还原成 Box（不是 dict）。

用法: python -u test_e2e_engines.py [engine_key ...]
默认跑 yoloworld locateanything florence2
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Keep test paths machine-independent. Override these environment variables
# when the test needs a dedicated model/runtime environment.
os.environ.setdefault(
    "DABIAO_DATA_DIR",
    os.path.abspath(os.environ.get("TAGFORGE_TEST_DATA_DIR", "test_dataset")),
)
os.environ.setdefault("DABIAO_SIDECAR_PYTHON", sys.executable)
os.environ["DABIAO_INSTALL_TARGET"] = "custom"

import app.engines.base as base
base.FROZEN = True  # 强制走 sidecar（等价于 exe 里 need_torch 引擎的路径）

from app.engines.base import Box, get_engine
from app.engines.sidecar import check_python_deps, current_python

CASES = {
    "yoloworld": dict(params={"classes": "person, bus, car", "conf": 0.15},
                      img=os.path.abspath("test_images/bus.jpg")),
    "locateanything": dict(params={"classes": "person, bus, car", "generation_mode": "slow", "max_new_tokens": 128},  # 显式类别检测
                           img=os.path.abspath("test_images/bus.jpg")),
    "florence2": dict(params={"caption": False, "objects": True, "phrases": ""},
                      img=os.path.abspath("test_images/night_02.jpg")),
}

keys = sys.argv[1:] or ["yoloworld", "locateanything", "florence2"]
fails = []

for key in keys:
    case = CASES[key]
    print(f"\n{'=' * 60}\n### {key}\n{'=' * 60}")
    try:
        eng = get_engine(key)
        exe = os.environ["DABIAO_SIDECAR_PYTHON"]
        missing = check_python_deps(exe, eng)
        print(f"缺依赖: {missing or '无'}")
        print(f"解释器: {current_python(eng)}")

        t0 = time.time()
        eng.load(case["params"], log_cb=lambda m: print("   [log]", m),
                 progress_cb=lambda v: None)
        print(f"加载耗时 {time.time() - t0:.1f}s")

        t1 = time.time()
        res = eng.tag_image(case["img"], case["params"])
        print(f"推理耗时 {time.time() - t1:.1f}s")

        bad = [b for b in res.boxes if not isinstance(b, Box)]
        assert not bad, f"框没被还原成 Box，仍是 {type(bad[0])}: {bad[:1]}"
        bad_geometry = [b for b in res.boxes if b.x2 <= b.x1 or b.y2 <= b.y1]
        assert not bad_geometry, f"存在无效零面积框: {bad_geometry[:2]}"
        print(f"tags ({len(res.tags)}): {res.tags[:15]}")
        print(f"boxes ({len(res.boxes)}):")
        for b in res.boxes[:8]:
            print(f"   {b.label:>14} conf={b.conf:.2f} "
                  f"({b.x1:.0f},{b.y1:.0f})-({b.x2:.0f},{b.y2:.0f})")

        # 第二张图：验换词表 / 重复推理不炸（历史 bug）
        if key == "yoloworld":
            p2 = dict(case["params"], classes="dog, cat, person")
            r2 = eng.tag_image(os.path.abspath("test_images/night_02.jpg"), p2)
            assert all(isinstance(b, Box) for b in r2.boxes)
            print(f"第二张图 OK: tags={r2.tags}, boxes={len(r2.boxes)}")

        eng.unload()
        print(">>> OK")
    except Exception as e:
        import traceback
        traceback.print_exc()
        fails.append(f"{key}: {e}")

print("\n" + "=" * 60)
print("失败:", fails if fails else "无")
sys.exit(1 if fails else 0)
