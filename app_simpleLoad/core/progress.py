"""进度推送器 — 封装 WebSocket 进度推送逻辑"""

import asyncio
from my_websockets.global_ws import ws
from loguru import logger


class ProgressReporter:
    """统一的 WebSocket 进度推送接口"""

    def __init__(self, client_id: str = "simple_load"):
        self.client_id = client_id

    async def send_text(self, message: str):
        """发送文本消息"""
        await ws.send_message(self.client_id, "text", message)

    async def send_progress(self, value: float):
        """发送进度百分比（0-100）"""
        await ws.send_message(self.client_id, "progress", f"{round(value, 1)}")

    async def update_smoothly(self, start: float, end: float, duration: float = 1.0):
        """平滑更新进度条，每 0.1 秒递增一次"""
        steps = max(int(duration * 10), 1)
        step_size = (end - start) / steps
        current = start

        for _ in range(steps):
            current += step_size
            success = await ws.send_message(
                self.client_id, "progress", f"{round(current, 1)}"
            )
            if not success:
                logger.warning("WebSocket 连接断开，停止进度更新")
                break
            await asyncio.sleep(0.1)

        # 确保最终进度准确
        await ws.send_message(self.client_id, "progress", f"{round(end, 1)}")
