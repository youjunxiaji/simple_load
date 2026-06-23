# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
import sys
from my_websockets import socket_routes
from app_simpleLoad.routes import router as simple_load_router
from my_websockets.socket_manager import ConnectionManager
from app_simpleLoad.core.logger import setup_logging, get_logger
import multiprocessing

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.websocket_manager = ConnectionManager()
    logger.info("WebSocket 管理器已初始化 - 实时通信就绪")
    yield
    logger.warning("Simple Load 系统正在关闭...")

app = FastAPI(
    title="Load Calculator API",
    lifespan=lifespan
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 包含路由
app.include_router(simple_load_router, prefix="/api")  # 添加 API 前缀
app.include_router(socket_routes.router)  # WebSocket 路由

console = Console()

def show_startup_banner(host="localhost", port=9000):
    """显示简洁的启动信息"""
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    info_table = Table(show_header=False, box=None, padding=(0, 1))
    info_table.add_column("key", style="bold cyan", width=10, justify="right")
    info_table.add_column("value")
    info_table.add_row("版本", "v1.2.2")
    info_table.add_row("团队", "Lei Gu & Hengshan Liu")
    info_table.add_row("Python", python_version)

    url_table = Table(show_header=False, box=None, padding=(0, 1))
    url_table.add_column("key", style="bold cyan", width=10, justify="right")
    url_table.add_column("value", style="underline bright_white")
    url_table.add_row("HTTP", f"http://{host}:{port}")
    url_table.add_row("WebSocket", f"ws://{host}:{port}/ws")
    url_table.add_row("API 文档", f"http://{host}:{port}/docs")

    body = Group(info_table, "", url_table)

    panel = Panel(
        body,
        title="[bold bright_white]载荷简化计算系统 · 系统已就绪[/]",
        border_style="bright_blue",
        expand=False,
        width=50,
        padding=(1, 2),
    )
    console.print(panel)


if __name__ == "__main__":
    multiprocessing.freeze_support()

    # 服务配置
    host = "0.0.0.0"
    port = 9000

    # --debug 启动参数：开启内存监控日志
    debug = "--debug" in sys.argv
    setup_logging(debug=debug)

    if debug:
        from app_simpleLoad.core.memory import set_enabled
        set_enabled(True)
        logger.info("DEBUG 模式已开启（内存监控启用）")

    show_startup_banner(host="localhost", port=port)
    uvicorn.run(
        app=app,
        host=host,
        port=port,
        log_config=None,
    )
