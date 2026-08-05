"""app_simpleLoad/core/progress.py —— WebSocket 进度推送"""

import pytest

from app_simpleLoad.core import progress as progress_module
from app_simpleLoad.core.progress import ProgressReporter
from my_websockets.global_ws import ws
from tests.fakes import FakeWebSocket


@pytest.fixture
def reporter() -> ProgressReporter:
    return ProgressReporter()


class TestSendMessages:
    async def test_默认客户端是_simple_load(self, reporter):
        assert reporter.client_id == "simple_load"

    async def test_发送文本消息(self, reporter, connected_ws):
        await reporter.send_text("开始处理")

        assert connected_ws.sent_json == [{"type": "text", "message": "开始处理"}]

    async def test_发送进度并保留一位小数(self, reporter, connected_ws):
        await reporter.send_progress(33.333333)

        assert connected_ws.sent_json == [{"type": "progress", "message": "33.3"}]

    async def test_没有连接时不报错(self, reporter):
        await reporter.send_text("没人听")
        await reporter.send_progress(50)

    async def test_可指定其他客户端(self):
        other = FakeWebSocket()
        ws.set_connection("another", other)

        await ProgressReporter("another").send_text("你好")

        assert other.texts == ["你好"]


class TestUpdateSmoothly:
    async def test_按_0_1_秒一步递增(self, reporter, connected_ws, instant_sleep):
        fake_asyncio = instant_sleep(progress_module)

        await reporter.update_smoothly(0, 50, duration=1.0)

        # 10 步 + 1 次收尾，且每步都真的 sleep 了 0.1 秒
        assert len(connected_ws.progresses) == 11
        assert fake_asyncio.slept == [0.1] * 10

    async def test_首尾值准确且单调递增(self, reporter, connected_ws, instant_sleep):
        instant_sleep(progress_module)

        await reporter.update_smoothly(20, 70, duration=0.5)

        values = connected_ws.progresses
        assert values[-1] == pytest.approx(70.0)
        assert values == sorted(values)
        assert values[0] == pytest.approx(30.0)      # 0.5s → 5 步，每步 +10

    async def test_时长过短也至少走一步(self, reporter, connected_ws, instant_sleep):
        instant_sleep(progress_module)

        await reporter.update_smoothly(0, 100, duration=0.0)

        assert connected_ws.progresses == [pytest.approx(100.0), pytest.approx(100.0)]

    async def test_连接断开后立即停止推送(self, reporter, instant_sleep):
        instant_sleep(progress_module)
        broken = FakeWebSocket(fail_on_send=True)
        ws.set_connection("simple_load", broken)

        await reporter.update_smoothly(0, 100, duration=5.0)

        assert broken.sent_json == []
        assert not ws.is_connection_active("simple_load")   # 失败后连接记录被清理
