from fastapi import WebSocket
from typing import Dict
from app_simpleLoad.core.logger import get_logger
import json

logger = get_logger(__name__)
from app_simpleLoad.module.cal_simpleLoad import CalSimpleLoad
from .global_ws import ws


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.cal_instance = None

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        ws.set_connection(client_id, websocket)
        logger.info(f"收到客户端连接请求: {client_id}")
        
        if client_id == 'simple_load':
            self.cal_instance = CalSimpleLoad()
        logger.info(f"客户端 {client_id} 连接成功")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            # 同时清理全局WebSocket管理器中的连接
            ws.remove_connection(client_id)
            if client_id == 'simple_load':
                logger.info("清理 CalSimpleLoad 实例")
                self.cal_instance = None
            logger.info(f"客户端 {client_id} 断开连接")

    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)
            logger.debug(f"向客户端 {client_id} 发送消息: {message}")

    async def broadcast(self, message: str):
        for client_id, connection in self.active_connections.items():
            try:
                await connection.send_text(message)
                logger.debug(f"广播消息到客户端 {client_id}")
            except Exception as e:
                logger.error(f"向客户端 {client_id} 发送消息失败: {str(e)}")

    def force_reset_instance(self, client_id: str):
        """强制重置实例和连接状态"""
        if client_id == 'simple_load':
            logger.info("强制重置 CalSimpleLoad 实例和WebSocket连接")
            self.cal_instance = None
            # 清理全局WebSocket连接
            ws.remove_connection(client_id)
            # 清理本地连接记录
            if client_id in self.active_connections:
                del self.active_connections[client_id]

    async def handle_command(self, message: Dict, client_id: str):
        """处理客户端发送的命令"""
        command = message.get("command")
        if command == "load_file":
            await self.send_personal_message(
                json.dumps({
                    "type": "response",
                    "command": "load_file",
                    "status": "processing"
                }),
                client_id
            )
        elif command == "reset_instance":
            # 处理重置实例命令
            self.force_reset_instance(client_id)
            await self.send_personal_message(
                json.dumps({
                    "type": "response",
                    "command": "reset_instance",
                    "status": "completed"
                }),
                client_id
            )
        # 添加其他命令处理...