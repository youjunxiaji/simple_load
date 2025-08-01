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
    logger.info("WebSocket 管理器已初始化")
    yield
    logger.info("应用关闭")

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

if __name__ == "__main__":
    multiprocessing.freeze_support()  # 添加这行
    logger.info("启动服务器")
    uvicorn.run(
        app="main:app",
        reload=True,  # 启用热重载
        reload_dirs=["./"],  # 监视的目录
        # app=app,
        host="0.0.0.0",
        port=9000,
        log_config=None
    )
