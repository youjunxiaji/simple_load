"""app_simpleLoad/core/memory.py —— 内存监控（默认关闭，--debug 才开）"""

import logging

import pytest

from app_simpleLoad.core import memory


class TestSwitch:
    def test_默认关闭(self):
        assert memory.is_enabled() is False

    def test_开关可切换(self):
        memory.set_enabled(True)
        assert memory.is_enabled() is True

        memory.set_enabled(False)
        assert memory.is_enabled() is False


class TestDisabled:
    def test_取内存返回零值(self):
        assert memory.get_memory_usage() == {"rss": 0, "vms": 0}

    def test_记录内存不写日志(self, caplog):
        with caplog.at_level(logging.INFO):
            result = memory.log_memory("预处理-开始")

        assert result == {"rss": 0, "vms": 0}
        assert caplog.records == []

    def test_峰值监控不启动线程(self):
        monitor = memory.MemoryMonitor()

        monitor.start("不该启动")

        assert monitor._thread is None
        assert monitor._running is False

    def test_未启动也能安全停止(self):
        result = memory.MemoryMonitor().stop()

        assert set(result) == {"start", "current", "peak", "delta"}


class TestEnabled:
    @pytest.fixture(autouse=True)
    def _enable(self):
        memory.set_enabled(True)

    def test_返回真实占用(self):
        usage = memory.get_memory_usage()

        assert usage["rss"] > 0
        assert usage["vms"] > 0

    def test_记录内存并写日志(self, caplog):
        with caplog.at_level(logging.INFO):
            memory.log_memory("预处理-开始")

        assert "预处理-开始" in caplog.text

    def test_带基线时输出变化量(self, caplog):
        start = memory.log_memory("开始")

        with caplog.at_level(logging.INFO):
            memory.log_memory("结束", start)

        assert "变化:" in caplog.text

    def test_峰值监控采样(self):
        monitor = memory.MemoryMonitor(interval=0.01)
        monitor.start("载荷缩减")

        blob = [0.0] * 200_000                     # 占点内存让峰值动起来
        result = monitor.stop()
        del blob

        assert result["peak"] >= result["current"]
        assert result["delta"] == pytest.approx(result["current"] - result["start"])
        assert monitor._running is False
