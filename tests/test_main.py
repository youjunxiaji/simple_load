"""main.py —— 应用装配与启动横幅"""

import io

from fastapi.middleware.cors import CORSMiddleware
from rich.console import Console

import main
from my_websockets.socket_manager import ConnectionManager


class TestAppWiring:
    def test_业务接口挂在_api_前缀下(self):
        paths = {route.path for route in main.app.routes}

        assert {"/api/load_file", "/api/divide_interval", "/api/reduce_load"} <= paths

    def test_websocket_路由已注册(self):
        paths = {route.path for route in main.app.routes}

        assert "/ws/{client_id}" in paths

    def test_开启了_CORS(self):
        assert any(m.cls is CORSMiddleware for m in main.app.user_middleware)

    def test_lifespan_里初始化连接管理器(self, api_client):
        assert isinstance(api_client.app.state.websocket_manager, ConnectionManager)


class TestStartupBanner:
    def test_横幅包含版本与访问地址(self, monkeypatch):
        buffer = io.StringIO()
        monkeypatch.setattr(main, "console", Console(file=buffer, width=120, no_color=True))

        main.show_startup_banner(host="localhost", port=9000)

        output = buffer.getvalue()
        assert "载荷简化计算系统" in output
        assert "http://localhost:9000" in output
        assert "ws://localhost:9000/ws" in output
        assert "http://localhost:9000/docs" in output
