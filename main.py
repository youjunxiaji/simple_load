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
import pandas as pd
import pyarrow

# 启用 Pandas Copy-on-Write 模式，优化内存使用
# 参考: https://pandas.pydata.org/docs/user_guide/copy_on_write.html
pd.options.mode.copy_on_write = True

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

def show_startup_banner(host="localhost", port=9000):
    """显示简洁的启动信息"""
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    print("=" * 60)
    print("🚀 SIMPLE LOAD SYSTEM - 载荷简化计算系统")
    print("=" * 60)
    print(f"系统版本:       1.0.8")
    print(f"开发团队:       Lei Gu & Hengshan Liu")
    print(f"Python版本:     {python_version}")
    print(f"FastAPI:        最新版")
    print("-" * 60)
    print(f"HTTP服务:       http://{host}:{port}")
    print(f"WebSocket:      ws://{host}:{port}/ws")
    print(f"管理界面:       http://{host}:{port}/docs")
    print("=" * 60)
    
    logger.success("🎉 Simple Load 系统启动成功！")
    logger.success("🌟 WebSocket 管理器已初始化")
    logger.success("✨ 系统已准备就绪，欢迎使用！")


if __name__ == "__main__":
    multiprocessing.freeze_support()  # 添加这行
    
    # 服务配置
    host = "0.0.0.0"
    port = 9000
    
    show_startup_banner(host="localhost", port=port)  # 显示localhost更友好
    uvicorn.run(
        # app="main:app",
        # reload=True,  # 启用热重载
        # reload_dirs=["./"],  # 监视的目录
        app=app,
        host=host,
        port=port,
        log_config=None
    )
