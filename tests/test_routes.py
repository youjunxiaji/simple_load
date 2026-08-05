"""app_simpleLoad/routes.py —— 三个 HTTP 接口

分支用 StubCalSimpleLoad 覆盖（快、只测路由逻辑），
最后一组用真实流水线跑一遍完整链路。
"""

import dataclasses
import json

import pytest

from app_simpleLoad import routes as routes_module
from app_simpleLoad.core.config import FileParseError
from tests.excel_io import read_gl
from tests.fakes import StubCalSimpleLoad
from tests.test_reduce_load import DEFAULT_BINS, table_data


def load_payload(dataset, config) -> dict:
    """构造 /api/load_file 的请求体。"""
    return {
        "file_path": {
            "result_folder_save_path": str(dataset.out_dir),
            "load_file_folder_path": str(dataset.load_dir),
            "freq_table_path": str(dataset.freq_path),
        },
        "draggableElements": [{"name": name} for name in dataset.header],
        "conversion_factors": dataclasses.asdict(config),
    }


def use_stub(api_client, monkeypatch, stub: StubCalSimpleLoad) -> StubCalSimpleLoad:
    """让路由里新建的实例变成 stub。"""
    monkeypatch.setattr(routes_module, "CalSimpleLoad", lambda: stub)
    return stub


class AlwaysConnectedWs:
    """routes 里 ws 的替身：连接始终正常。"""

    def __init__(self, active_after: int = 0) -> None:
        self.active_after = active_after
        self.checks = 0

    def is_connection_active(self, client_id: str) -> bool:
        self.checks += 1
        return self.checks > self.active_after


# ─── /api/load_file ──────────────────────────────────────────

class TestLoadFile:
    def test_连接正常时新建实例并加载(self, api_client, monkeypatch, dataset, config):
        monkeypatch.setattr(routes_module, "ws", AlwaysConnectedWs())
        stub = use_stub(api_client, monkeypatch, StubCalSimpleLoad())

        response = api_client.post("/api/load_file", json=load_payload(dataset, config))

        assert response.json() == {"message": "读取文件完成", "status": "success"}
        assert api_client.app.state.websocket_manager.cal_instance is stub
        assert [name for name, _, _ in stub.calls] == ["setInit", "simple_Pre_processing"]

    def test_把前端参数原样传给实例(self, api_client, monkeypatch, dataset, config):
        monkeypatch.setattr(routes_module, "ws", AlwaysConnectedWs())
        stub = use_stub(api_client, monkeypatch, StubCalSimpleLoad())

        api_client.post("/api/load_file", json=load_payload(dataset, config))

        kwargs = stub.calls[0][2]
        assert kwargs["header"] == dataset.header
        assert kwargs["paths"].freq_table_path == str(dataset.freq_path)
        assert kwargs["config"].translate_factor == config.translate_factor

    def test_已有实例时复用(self, api_client, monkeypatch, dataset, config):
        existing = StubCalSimpleLoad()
        api_client.app.state.websocket_manager.cal_instance = existing
        monkeypatch.setattr(routes_module, "CalSimpleLoad", lambda: pytest.fail("不应新建实例"))

        response = api_client.post("/api/load_file", json=load_payload(dataset, config))

        assert response.json()["status"] == "success"
        assert api_client.app.state.websocket_manager.cal_instance is existing

    def test_连接断开且等不到重连时提示刷新(self, api_client, monkeypatch, dataset, config, instant_sleep):
        instant_sleep(routes_module)
        monkeypatch.setattr(routes_module, "ws", AlwaysConnectedWs(active_after=999))

        response = api_client.post("/api/load_file", json=load_payload(dataset, config))

        assert response.json() == {
            "message": "WebSocket连接已断开，请刷新页面重新连接",
            "status": "error",
            "need_reconnect": True,
        }
        assert api_client.app.state.websocket_manager.cal_instance is None

    def test_等待期间重连成功则继续处理(self, api_client, monkeypatch, dataset, config, instant_sleep):
        instant_sleep(routes_module)
        monkeypatch.setattr(routes_module, "ws", AlwaysConnectedWs(active_after=3))
        use_stub(api_client, monkeypatch, StubCalSimpleLoad())

        response = api_client.post("/api/load_file", json=load_payload(dataset, config))

        assert response.json()["status"] == "success"

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (ValueError("标题配置错误：缺少必需的列 ['Fx[KN]']"), "标题配置错误：缺少必需的列 ['Fx[KN]']"),
        ],
    )
    def test_setInit_报错时返回错误信息(
        self, api_client, monkeypatch, dataset, config, error, expected
    ):
        monkeypatch.setattr(routes_module, "ws", AlwaysConnectedWs())
        use_stub(api_client, monkeypatch, StubCalSimpleLoad(set_init_error=error))

        response = api_client.post("/api/load_file", json=load_payload(dataset, config))

        assert response.json() == {"message": expected, "status": "error"}

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (ValueError("频次表与载荷文件不匹配：..."), "频次表与载荷文件不匹配：..."),
            (FileParseError("c1.txt", "列数不匹配"), "文件 c1.txt 解析失败: 列数不匹配"),
            (RuntimeError("磁盘炸了"), "文件加载失败: 磁盘炸了"),
        ],
    )
    def test_预处理各类异常都转成错误响应(
        self, api_client, monkeypatch, dataset, config, error, expected
    ):
        monkeypatch.setattr(routes_module, "ws", AlwaysConnectedWs())
        use_stub(api_client, monkeypatch, StubCalSimpleLoad(pre_processing_error=error))

        response = api_client.post("/api/load_file", json=load_payload(dataset, config))

        assert response.json() == {"message": expected, "status": "error"}


# ─── /api/divide_interval ────────────────────────────────────

class TestDivideInterval:
    def test_未加载文件时提示(self, api_client):
        response = api_client.post("/api/divide_interval", json={"romax_origin": []})

        assert response.json() == {"message": "请先加载文件", "status": "error"}

    def test_返回最值与图表数据(self, api_client, romax_origin):
        stub = StubCalSimpleLoad(load1_result='{"min":{}}', save_pic_result={"Fx[KN]": "{}"})
        api_client.app.state.websocket_manager.cal_instance = stub

        response = api_client.post("/api/divide_interval", json={"romax_origin": romax_origin})

        assert response.json() == {
            "message": "划分区间完成",
            "min_max": '{"min":{}}',
            "echarts_data": {"Fx[KN]": "{}"},
            "status": "success",
        }
        assert stub.calls[0] == ("simple_load1", (romax_origin,), {})

    def test_缺省_romax_origin_时传空列表(self, api_client):
        stub = StubCalSimpleLoad()
        api_client.app.state.websocket_manager.cal_instance = stub

        api_client.post("/api/divide_interval", json={})

        assert stub.calls[0] == ("simple_load1", ([],), {})

    def test_实例状态不全时提示先加载文件(self, api_client):
        """savePic 在未划分区间时抛 AttributeError，路由兜住它。"""
        stub = StubCalSimpleLoad(load1_error=AttributeError("max_min"))
        api_client.app.state.websocket_manager.cal_instance = stub

        response = api_client.post("/api/divide_interval", json={"romax_origin": []})

        assert response.json() == {"message": "请先加载文件", "status": "error"}


# ─── /api/reduce_load ────────────────────────────────────────

class TestReduceLoad:
    def test_未加载文件时提示(self, api_client):
        response = api_client.post("/api/reduce_load", json={"tableData": [], "romax_origin": []})

        assert response.json() == {"message": "请先加载文件", "status": "error"}

    def test_返回缩减后的工况数(self, api_client, romax_origin):
        stub = StubCalSimpleLoad(load2_result=42)
        api_client.app.state.websocket_manager.cal_instance = stub

        response = api_client.post(
            "/api/reduce_load", json={"tableData": [{"0": "1"}], "romax_origin": romax_origin}
        )

        assert response.json() == {"message": "载荷简化处理全部完成", "count": 42}
        assert stub.calls[0] == ("simple_load2", ([{"0": "1"}], romax_origin), {})

    def test_业务校验失败时透传错误信息(self, api_client, romax_origin):
        stub = StubCalSimpleLoad(
            load2_result={"message": "Fx[KN]的区间值必须是单调递增的", "status": "error"}
        )
        api_client.app.state.websocket_manager.cal_instance = stub

        response = api_client.post(
            "/api/reduce_load", json={"tableData": [], "romax_origin": romax_origin}
        )

        assert response.json() == {"message": "Fx[KN]的区间值必须是单调递增的", "status": "error"}

    def test_计算异常时返回失败原因(self, api_client, romax_origin):
        stub = StubCalSimpleLoad(load2_error=IndexError("list index out of range"))
        api_client.app.state.websocket_manager.cal_instance = stub

        response = api_client.post(
            "/api/reduce_load", json={"tableData": [], "romax_origin": romax_origin}
        )

        assert response.json() == {
            "message": "载荷缩减失败: list index out of range",
            "status": "error",
        }


# ─── 完整链路（真实计算） ────────────────────────────────────

class TestFullFlowThroughApi:
    def test_加载到导出三步走通(
        self, api_client, monkeypatch, dataset, config, romax_origin, instant_sleep
    ):
        from app_simpleLoad.core import progress as progress_module

        instant_sleep(progress_module)
        monkeypatch.setattr(routes_module, "ws", AlwaysConnectedWs())

        load = api_client.post("/api/load_file", json=load_payload(dataset, config))
        assert load.json()["status"] == "success"

        divide = api_client.post("/api/divide_interval", json={"romax_origin": romax_origin})
        body = divide.json()
        assert body["status"] == "success"
        assert set(json.loads(body["min_max"])) == {"min", "max"}
        assert set(body["echarts_data"]) == {
            "Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]", "Mz[KNm]",
        }

        reduce_ = api_client.post(
            "/api/reduce_load",
            json={"tableData": table_data(*DEFAULT_BINS), "romax_origin": romax_origin},
        )
        assert reduce_.json()["message"] == "载荷简化处理全部完成"
        assert reduce_.json()["count"] == len(read_gl(dataset.gl_excel()))
