"""pytest 公共 fixture

原则：
1. 每个测试都跑在自己的临时目录里，互不干扰；
2. 全局单例（WebSocket 连接表、日志 handler、内存监控开关）在每个用例
   前后自动复位，避免用例之间互相污染；
3. 默认**不建立** WebSocket 连接 —— 此时 `ProgressReporter` 第一次发送
   就失败并中断 sleep 循环，整套流水线测试因此是毫秒级的。
"""
import asyncio
import logging
from pathlib import Path

import pytest

from app_simpleLoad.core import memory as memory_module
from app_simpleLoad.core.config import ConversionConfig
from app_simpleLoad.module.cal_simpleLoad import CalSimpleLoad
from my_websockets.global_ws import GlobalWebSocket, ws
from tests import factories
from tests.factories import CaseSpec, Dataset, build_dataset, ramp_case
from tests.fakes import FakeWebSocket, InstantSleepAsyncio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_CASE_DIR = PROJECT_ROOT / "测试案例"


# ─── 全局状态隔离 ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_global_ws():
    """GlobalWebSocket._connections 是类属性，用例之间必须清干净。"""
    GlobalWebSocket._connections.clear()
    yield
    GlobalWebSocket._connections.clear()


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """setup_logging() 会清空 root handler（含 pytest 的日志捕获），用完还原。"""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    third_party = {
        name: logging.getLogger(name).level
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "websockets")
    }
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, lvl in third_party.items():
        logging.getLogger(name).setLevel(lvl)


@pytest.fixture(autouse=True)
def _restore_memory_switch():
    """内存监控是模块级全局开关，测试改动后还原。"""
    original = memory_module.is_enabled()
    yield
    memory_module.set_enabled(original)


# ─── 基础配置 ────────────────────────────────────────────────

@pytest.fixture
def header() -> list[str]:
    """前端传来的列映射（draggableElements 顺序）。"""
    return list(factories.DEFAULT_HEADER)


@pytest.fixture
def config() -> ConversionConfig:
    """典型换算配置：力/力矩 N→KN、Nm→KNm，S-N 斜率 4。"""
    return ConversionConfig(
        title_row=0,
        unit_moment=1000.0,
        unit_force=1000.0,
        unit_speed=1.0,
        translate_factor=4.0,
        temperature=40.0,
        tol=1e-6,
    )


@pytest.fixture
def romax_origin() -> list[dict[str, str]]:
    """Romax 坐标映射：x←x, y←-z, z←y（即排除原始 y 轴力矩 My）。"""
    return [dict(item) for item in factories.ROMAX_ORIGIN]


# ─── 合成数据集 ──────────────────────────────────────────────

@pytest.fixture
def dataset(tmp_path: Path) -> Dataset:
    """两个工况、各 4 行的确定性数据集（含 Time 列）。

    - c1 正值、发生次数 100；c2 负值、发生次数 200
    - 工况占比 = 1/3 与 2/3，便于手算
    """
    return build_dataset(
        tmp_path,
        [
            ramp_case("c1", n_rows=4, occurrences=100.0, speed=10.0, sign=1.0),
            ramp_case("c2", n_rows=4, occurrences=200.0, speed=20.0, sign=-1.0, offset=500.0),
        ],
    )


@pytest.fixture
def dataset_no_time(tmp_path: Path) -> Dataset:
    """不含 Time 列的数据集：采样间隔需由频次表的仿真时间反推。"""
    header = [c for c in factories.DEFAULT_HEADER if c != "Time[s]"]
    cases = [
        ramp_case("c1", n_rows=4, occurrences=100.0, speed=10.0, with_time=False),
        ramp_case("c2", n_rows=4, occurrences=200.0, speed=20.0, sign=-1.0, with_time=False),
    ]
    for case in cases:
        case.sim_time = 0.3
    return build_dataset(tmp_path, cases, header=header)


def make_instance(dataset: Dataset, config: ConversionConfig) -> CalSimpleLoad:
    """按数据集参数构造一个已 setInit 的实例（未加载数据）。"""
    instance = CalSimpleLoad()
    instance.setInit(paths=dataset.path_config(), header=dataset.header, config=config)
    return instance


@pytest.fixture
def instance(dataset: Dataset, config: ConversionConfig) -> CalSimpleLoad:
    """已 setInit、尚未读文件的实例。"""
    return make_instance(dataset, config)


@pytest.fixture
async def loaded(instance: CalSimpleLoad) -> CalSimpleLoad:
    """已完成步骤一（加载与预处理）的实例。"""
    await instance.simple_Pre_processing()
    return instance


@pytest.fixture
async def divided(loaded: CalSimpleLoad, romax_origin) -> CalSimpleLoad:
    """已完成步骤二（划分区间）的实例，可直接调用 savePic / simple_load2。"""
    await loaded.simple_load1(romax_origin)
    return loaded


# ─── WebSocket / 进度 ────────────────────────────────────────

@pytest.fixture
def fake_ws() -> FakeWebSocket:
    """未注册到 GlobalWebSocket 的假连接。"""
    return FakeWebSocket()


@pytest.fixture
def connected_ws(fake_ws: FakeWebSocket) -> FakeWebSocket:
    """已注册为 client_id='simple_load' 的假连接（进度推送会被记录）。"""
    ws.set_connection("simple_load", fake_ws)
    return fake_ws


@pytest.fixture
def instant_sleep(monkeypatch):
    """把某个模块里的 asyncio.sleep 变成瞬时返回。

    用法::

        instant_sleep(progress_module)      # 之后 update_smoothly 不再真的等待
    """

    def _patch(module) -> InstantSleepAsyncio:
        fake = InstantSleepAsyncio(asyncio)
        monkeypatch.setattr(module, "asyncio", fake)
        return fake

    return _patch


# ─── HTTP 客户端 ─────────────────────────────────────────────

@pytest.fixture
def api_client():
    """带 lifespan 的 FastAPI 测试客户端（app.state.websocket_manager 已就绪）。"""
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        client.app.state.websocket_manager.cal_instance = None
        yield client
        client.app.state.websocket_manager.cal_instance = None


# ─── 真实测试案例（可选） ────────────────────────────────────

@pytest.fixture(scope="session")
def real_case_dir() -> Path:
    """本地 `测试案例/` 目录；不存在时跳过（该目录不入库）。"""
    folder = REAL_CASE_DIR / "GW-V16-MB-适应性分析载荷-20260210"
    freq = REAL_CASE_DIR / "GW-V16-MB-适应性分析载荷-20260210.xlsx"
    if not folder.is_dir() or not freq.is_file():
        pytest.skip("本地未提供 测试案例/ 数据，跳过真实数据回归")
    return REAL_CASE_DIR
