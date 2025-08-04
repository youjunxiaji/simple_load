# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
from loguru import logger
import sys
from my_websockets import socket_routes
from app_simpleLoad.routes import router as simple_load_router
from my_websockets.socket_manager import ConnectionManager
import multiprocessing

# 配置 loguru
logger.remove()  # 移除默认的处理器
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    colorize=True
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.websocket_manager = ConnectionManager()
    logger.success("🚀 WebSocket 管理器已初始化 - 实时通信就绪")
    yield
    logger.warning("📴 Simple Load 系统正在关闭...")

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

def show_startup_banner():
    """显示酷炫的启动界面"""
    banner = r"""
╭─ Simple Load System ───────────────────────────────────────────────────────╮
│                                                                            │
│     _____ _____ __  __ _____  _      ______   _      ____          _____   │
│    / ____|_   _|  \/  |  __ \| |    |  ____| | |    / __ \   /\   |  __ \  │
│   | (___   | | | \  / | |__) | |    | |__    | |   | |  | | /  \  | |  | | │
│    \___ \  | | | |\/| |  ___/| |    |  __|   | |   | |  | |/ /\ \ | |  | | │
│    ____) |_| |_| |  | | |    | |____| |____  | |___| |__| / ____ \| |__| | │
│   |_____/|_____|_|  |_|_|    |______|______| |______\____/_/    \_\_____/  │
│                                                                            │
│                          Simple Load Calculation System                    │
│                                                                            │
│    👨‍💻 软件开发:        Lei Gu                                            │
│    📚 理论支持:        Hengshan Liu                                        │
│                                                                            │
│    🌐 服务地址:        http://0.0.0.0:9000                                 │
│    📡 WebSocket       ws://0.0.0.0:9000/ws                                 │
│                                                                            │
│    🚀 FastAPI版本:     最新版                                              │
│    ⚡ Python版本:      {python_version}                                              │
│                                                                            │
╰────────────────────────────────────────────────────────────────────────────╯
""".format(python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    
    print(banner)
    logger.success("🎉 Simple Load 系统启动成功！")
    logger.success("🌟 WebSocket 管理器已初始化")
    logger.success("✨ 系统已准备就绪，欢迎使用！")


if __name__ == "__main__":
    multiprocessing.freeze_support()  # 添加这行
    show_startup_banner()
    uvicorn.run(
        # app="main:app",
        # reload=True,  # 启用热重载
        # reload_dirs=["./"],  # 监视的目录
        app=app,
        host="0.0.0.0",
        port=9000,
        log_config=None
    )
