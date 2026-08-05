"""测试替身（fake / stub）

只放「假对象」，不放 fixture —— fixture 在 conftest.py 里组装。
"""
import asyncio
from types import SimpleNamespace
from typing import Any


class FakeWebSocket:
    """FastAPI WebSocket 的最小替身。

    记录 accept / send_json / send_text 调用，可通过 state 模拟连接状态，
    通过 fail_on_send 模拟发送异常。
    """

    def __init__(
        self,
        state: str | None = "CONNECTED",
        *,
        fail_on_send: bool = False,
        has_client_state: bool = True,
        has_state: bool = False,
    ) -> None:
        self.accepted = False
        self.sent_json: list[dict[str, Any]] = []
        self.sent_text: list[str] = []
        self.fail_on_send = fail_on_send
        if has_client_state:
            self.client_state = SimpleNamespace(name=state)
        if has_state:
            self.state = SimpleNamespace(name=state)

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.fail_on_send:
            raise RuntimeError("连接已关闭")
        self.sent_json.append(payload)

    async def send_text(self, message: str) -> None:
        if self.fail_on_send:
            raise RuntimeError("连接已关闭")
        self.sent_text.append(message)

    # ── 便捷断言 ──────────────────────────────────────────
    def messages(self, message_type: str) -> list[str]:
        return [m["message"] for m in self.sent_json if m["type"] == message_type]

    @property
    def texts(self) -> list[str]:
        return self.messages("text")

    @property
    def progresses(self) -> list[float]:
        return [float(v) for v in self.messages("progress")]


class BareWebSocket:
    """既没有 client_state 也没有 state 的连接对象（走 hasattr(send_json) 兜底分支）。"""

    def __init__(self) -> None:
        self.sent_json: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent_json.append(payload)


class OpaqueWebSocket:
    """访问 client_state 就抛异常，用于验证状态检查的异常兜底。"""

    @property
    def client_state(self):  # pragma: no cover - 属性访问即抛错
        raise RuntimeError("状态不可读")


class RecordingProgress:
    """ProgressReporter 的替身：只记录，不真发 WebSocket。"""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.progresses: list[float] = []
        self.smooth_calls: list[tuple[float, float, float]] = []

    async def send_text(self, message: str) -> None:
        self.texts.append(message)

    async def send_progress(self, value: float) -> None:
        self.progresses.append(float(value))

    async def update_smoothly(self, start: float, end: float, duration: float = 1.0) -> None:
        self.smooth_calls.append((start, end, duration))
        self.progresses.append(float(end))


class StubCalSimpleLoad:
    """CalSimpleLoad 的替身，供 routes 分支测试使用。

    每个方法既可以返回预设值，也可以抛出预设异常。
    """

    def __init__(
        self,
        *,
        set_init_error: Exception | None = None,
        pre_processing_error: Exception | None = None,
        load1_result: Any = "{}",
        load1_error: Exception | None = None,
        save_pic_result: Any = None,
        load2_result: Any = 3,
        load2_error: Exception | None = None,
    ) -> None:
        self.set_init_error = set_init_error
        self.pre_processing_error = pre_processing_error
        self.load1_result = load1_result
        self.load1_error = load1_error
        self.save_pic_result = save_pic_result if save_pic_result is not None else {}
        self.load2_result = load2_result
        self.load2_error = load2_error
        self.calls: list[tuple[str, tuple, dict]] = []

    def setInit(self, **kwargs: Any) -> None:
        self.calls.append(("setInit", (), kwargs))
        if self.set_init_error:
            raise self.set_init_error

    async def simple_Pre_processing(self) -> None:
        self.calls.append(("simple_Pre_processing", (), {}))
        if self.pre_processing_error:
            raise self.pre_processing_error

    async def simple_load1(self, romax_origin: Any) -> Any:
        self.calls.append(("simple_load1", (romax_origin,), {}))
        if self.load1_error:
            raise self.load1_error
        return self.load1_result

    async def savePic(self) -> Any:
        self.calls.append(("savePic", (), {}))
        return self.save_pic_result

    async def simple_load2(self, table_data: Any, romax_origin: Any) -> Any:
        self.calls.append(("simple_load2", (table_data, romax_origin), {}))
        if self.load2_error:
            raise self.load2_error
        return self.load2_result


class InstantSleepAsyncio:
    """asyncio 模块的替身：sleep 立即返回，其余属性透传给真 asyncio。

    用于把「平滑进度条 / 等待重连」这类 sleep 循环压缩到 0 耗时::

        monkeypatch.setattr(progress_module, "asyncio", InstantSleepAsyncio())
    """

    def __init__(self, real: Any = asyncio) -> None:
        self._real = real
        self.slept: list[float] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def sleep(self, delay: float, result: Any = None) -> Any:
        self.slept.append(delay)
        return result
