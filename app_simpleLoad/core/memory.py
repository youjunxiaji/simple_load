"""内存监控模块 — 可选的调试工具

使用方法：
    from app_simpleLoad.core.memory import log_memory, MemoryMonitor, set_enabled

    set_enabled(False)  # 关闭所有内存监控（生产模式）

    # 快照式监控
    start = log_memory("开始")
    ...
    log_memory("结束", start)

    # 峰值监控（后台线程每 0.5 秒采样）
    monitor = MemoryMonitor()
    monitor.start("文件读取")
    ...
    result = monitor.stop()  # → {'start': 100, 'current': 200, 'peak': 350, 'delta': +100}
"""

import threading
import time
from app_simpleLoad.core.logger import get_logger
import psutil

logger = get_logger(__name__)

# ─── 全局开关（默认关闭，生产环境无性能开销） ──────────────

_enabled = False


def set_enabled(value: bool):
    """开启/关闭内存监控（关闭后所有函数变为 no-op）"""
    global _enabled
    _enabled = value


def is_enabled() -> bool:
    return _enabled


# ─── 快照式监控 ──────────────────────────────────────────────

def get_memory_usage() -> dict:
    """获取当前进程内存使用情况 (MB)"""
    if not _enabled:
        return {"rss": 0, "vms": 0}
    process = psutil.Process()
    info = process.memory_info()
    return {
        "rss": info.rss / 1024 / 1024,
        "vms": info.vms / 1024 / 1024,
    }


def log_memory(stage: str, memory_start: dict | None = None) -> dict:
    """记录内存快照并输出日志"""
    if not _enabled:
        return {"rss": 0, "vms": 0}
    current = get_memory_usage()
    if memory_start:
        delta_rss = current["rss"] - memory_start["rss"]
        logger.info(
            f"[{stage}] 内存: {current['rss']:.1f}MB (变化: {delta_rss:+.1f}MB)"
        )
    else:
        logger.info(f"[{stage}] 内存: {current['rss']:.1f}MB")
    return current


# ─── 峰值监控（后台线程） ────────────────────────────────────

class MemoryMonitor:
    """后台线程持续采样，记录内存峰值"""

    def __init__(self, interval: float = 0.5):
        self._interval = interval
        self._peak_rss = 0.0
        self._start_rss = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._process = psutil.Process()
        self._label = ""

    def start(self, label: str = ""):
        """开始后台监控"""
        if not _enabled:
            return
        current = self._process.memory_info().rss / 1024 / 1024
        self._peak_rss = current
        self._start_rss = current
        self._label = label
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self):
        while self._running:
            rss = self._process.memory_info().rss / 1024 / 1024
            if rss > self._peak_rss:
                self._peak_rss = rss
            time.sleep(self._interval)

    def stop(self) -> dict:
        """停止监控并返回结果"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        current = self._process.memory_info().rss / 1024 / 1024
        peak = max(self._peak_rss, current)  # 确保峰值 >= 当前值
        result = {
            "start": self._start_rss,
            "current": current,
            "peak": peak,
            "delta": current - self._start_rss,
        }
        if _enabled:
            logger.info(
                f"[{self._label}] "
                f"当前: {current:.1f}MB, "
                f"峰值: {self._peak_rss:.1f}MB, "
                f"变化: {result['delta']:+.1f}MB"
            )
        return result
