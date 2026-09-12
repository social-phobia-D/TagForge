import shutil
import subprocess


_GPU_CACHE = None


def gpu_info() -> tuple:
    """返回 (显卡名, 显存MB)，无 N 卡返回 (None, 0)。
    nvidia-smi 是子进程调用（可能秒级），结果进程内缓存，只查一次。"""
    global _GPU_CACHE
    if _GPU_CACHE is not None:
        return _GPU_CACHE
    name, mb = None, 0
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            line = out.stdout.strip().splitlines()[0]
            name, mb = line.split(",")[0].strip(), int(line.split(",")[1].strip())
        except Exception:
            pass
    _GPU_CACHE = (name, mb)
    return _GPU_CACHE


def pick_precision(vram_mb: int) -> str:
    """按显存选精度：fp16 / int4 / cpu"""
    if vram_mb >= 16000:
        return "fp16"
    if vram_mb >= 6000:
        return "int4"
    return "cpu"


def torch_cuda_available() -> bool:
    """torch 可能未安装，安全探测"""
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False
