"""my_websockets —— 全局连接单例、连接管理器、WebSocket 路由"""

import json

import pytest

from app_simpleLoad.module.cal_simpleLoad import CalSimpleLoad
from my_websockets.global_ws import GlobalWebSocket, ws
from my_websockets.socket_manager import ConnectionManager
from tests.fakes import BareWebSocket, FakeWebSocket, OpaqueWebSocket


# ─── 全局连接单例 ────────────────────────────────────────────

class TestGlobalWebSocket:
    def test_是单例(self):
        assert GlobalWebSocket() is GlobalWebSocket()
        assert isinstance(ws, GlobalWebSocket)

    def test_注册与移除连接(self, fake_ws):
        ws.set_connection("simple_load", fake_ws)
        assert ws.is_connection_active("simple_load")

        ws.remove_connection("simple_load")
        assert not ws.is_connection_active("simple_load")

    def test_移除不存在的连接不报错(self):
        ws.remove_connection("从未注册")

    @pytest.mark.parametrize(
        ("state", "expected"),
        [("CONNECTED", True), ("DISCONNECTED", False), ("CONNECTING", False)],
    )
    def test_按_client_state_判断是否活跃(self, state, expected):
        ws.set_connection("c", FakeWebSocket(state=state))

        assert ws.is_connection_active("c") is expected

    def test_退化到_state_属性(self):
        ws.set_connection("c", FakeWebSocket(state="CONNECTED", has_client_state=False, has_state=True))

        assert ws.is_connection_active("c") is True

    def test_两个状态属性都没有时看能否发送(self):
        ws.set_connection("c", BareWebSocket())

        assert ws.is_connection_active("c") is True

    def test_状态读取异常时视为不活跃(self):
        ws.set_connection("c", OpaqueWebSocket())

        assert ws.is_connection_active("c") is False

    async def test_发送消息的报文格式(self, connected_ws):
        result = await ws.send_message("simple_load", "text", "你好")

        assert result is True
        assert connected_ws.sent_json == [{"type": "text", "message": "你好"}]

    async def test_客户端不存在时返回_False(self):
        assert await ws.send_message("不存在", "text", "你好") is False

    async def test_连接已断开时清理记录(self):
        ws.set_connection("simple_load", FakeWebSocket(state="DISCONNECTED"))

        assert await ws.send_message("simple_load", "text", "你好") is False
        assert "simple_load" not in GlobalWebSocket._connections

    async def test_发送异常时清理记录(self):
        ws.set_connection("simple_load", FakeWebSocket(fail_on_send=True))

        assert await ws.send_message("simple_load", "progress", "50") is False
        assert "simple_load" not in GlobalWebSocket._connections


# ─── 连接管理器 ──────────────────────────────────────────────

class TestConnectionManager:
    @pytest.fixture
    def manager(self) -> ConnectionManager:
        return ConnectionManager()

    async def test_连接时接受握手并登记(self, manager, fake_ws):
        await manager.connect(fake_ws, "simple_load")

        assert fake_ws.accepted
        assert manager.active_connections["simple_load"] is fake_ws
        assert GlobalWebSocket._connections["simple_load"] is fake_ws

    async def test_simple_load_连接时创建计算实例(self, manager, fake_ws):
        await manager.connect(fake_ws, "simple_load")

        assert isinstance(manager.cal_instance, CalSimpleLoad)

    async def test_其他客户端不创建计算实例(self, manager, fake_ws):
        await manager.connect(fake_ws, "other")

        assert manager.cal_instance is None

    async def test_断开时清理连接与实例(self, manager, fake_ws):
        await manager.connect(fake_ws, "simple_load")

        manager.disconnect("simple_load")

        assert manager.active_connections == {}
        assert "simple_load" not in GlobalWebSocket._connections
        assert manager.cal_instance is None

    async def test_断开未知客户端不报错(self, manager):
        manager.disconnect("未知")

    async def test_点对点发送(self, manager, fake_ws):
        await manager.connect(fake_ws, "simple_load")

        await manager.send_personal_message("hi", "simple_load")

        assert fake_ws.sent_text == ["hi"]

    async def test_点对点发送给未连接的客户端会被忽略(self, manager):
        await manager.send_personal_message("hi", "未连接")

    async def test_广播给所有客户端(self, manager):
        a, b = FakeWebSocket(), FakeWebSocket()
        await manager.connect(a, "a")
        await manager.connect(b, "b")

        await manager.broadcast("公告")

        assert a.sent_text == ["公告"] and b.sent_text == ["公告"]

    async def test_单个客户端发送失败不影响其他客户端(self, manager):
        broken, good = FakeWebSocket(fail_on_send=True), FakeWebSocket()
        await manager.connect(broken, "broken")
        await manager.connect(good, "good")

        await manager.broadcast("公告")

        assert good.sent_text == ["公告"]

    async def test_load_file_命令回执(self, manager, fake_ws):
        await manager.connect(fake_ws, "simple_load")

        await manager.handle_command({"type": "command", "command": "load_file"}, "simple_load")

        assert json.loads(fake_ws.sent_text[0]) == {
            "type": "response", "command": "load_file", "status": "processing",
        }

    async def test_reset_instance_命令清空实例(self, manager, fake_ws):
        await manager.connect(fake_ws, "simple_load")

        await manager.handle_command({"type": "command", "command": "reset_instance"}, "simple_load")

        assert manager.cal_instance is None
        assert manager.active_connections == {}
        assert "simple_load" not in GlobalWebSocket._connections
        # 连接记录已被清掉，所以回执发不出去（前端靠 cal_instance 为空重新走加载流程）
        assert fake_ws.sent_text == []

    async def test_未知命令被忽略(self, manager, fake_ws):
        await manager.connect(fake_ws, "simple_load")

        await manager.handle_command({"type": "command", "command": "不存在"}, "simple_load")

        assert fake_ws.sent_text == []

    def test_强制重置只作用于_simple_load(self, manager):
        manager.cal_instance = object()

        manager.force_reset_instance("other")

        assert manager.cal_instance is not None


# ─── WebSocket 路由 ──────────────────────────────────────────

class TestWebSocketRoute:
    def test_连接后进入连接表(self, api_client):
        manager = api_client.app.state.websocket_manager

        with api_client.websocket_connect("/ws/simple_load"):
            assert "simple_load" in manager.active_connections
            assert isinstance(manager.cal_instance, CalSimpleLoad)

    def test_断开后清理连接(self, api_client):
        manager = api_client.app.state.websocket_manager

        with api_client.websocket_connect("/ws/simple_load"):
            pass

        assert manager.active_connections == {}
        assert manager.cal_instance is None

    def test_广播消息回传(self, api_client):
        with api_client.websocket_connect("/ws/simple_load") as socket:
            socket.send_text(json.dumps({"type": "broadcast", "message": "你好"}))

            assert json.loads(socket.receive_text()) == {"type": "broadcast", "message": "你好"}

    def test_非法_JSON_不会断开连接(self, api_client):
        with api_client.websocket_connect("/ws/simple_load") as socket:
            socket.send_text("这不是 JSON")
            socket.send_text(json.dumps({"type": "broadcast", "message": "还活着"}))

            assert json.loads(socket.receive_text())["message"] == "还活着"

    def test_命令消息走_handle_command(self, api_client):
        with api_client.websocket_connect("/ws/simple_load") as socket:
            socket.send_text(json.dumps({"type": "command", "command": "load_file"}))

            assert json.loads(socket.receive_text())["command"] == "load_file"
