from typing import Dict, Literal
from fastapi import WebSocket
from app_simpleLoad.core.logger import get_logger
import json

logger = get_logger(__name__)


class GlobalWebSocket:
    _instance = None
    _connections: Dict[str, WebSocket] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GlobalWebSocket, cls).__new__(cls)
        return cls._instance

    @classmethod
    def set_connection(cls, client_id: str, websocket: WebSocket):
        cls._connections[client_id] = websocket
        logger.info(f"已设置客户端 {client_id} 的 WebSocket 连接")

    @classmethod
    def remove_connection(cls, client_id: str):
        if client_id in cls._connections:
            del cls._connections[client_id]
            logger.info(f"已移除客户端 {client_id} 的 WebSocket 连接")

    @classmethod
    def is_connection_active(cls, client_id: str) -> bool:
        """检查WebSocket连接是否活跃"""
        if client_id not in cls._connections:
            return False
        
        websocket = cls._connections[client_id]
        try:
            # 检查WebSocket状态 - 使用更准确的检查方式
            if hasattr(websocket, 'client_state'):
                state_name = websocket.client_state.name if hasattr(websocket.client_state, 'name') else str(websocket.client_state)
                return state_name == 'CONNECTED'
            elif hasattr(websocket, 'state'):
                # 备用检查方式
                return websocket.state.name == 'CONNECTED'
            else:
                # 如果无法检查状态，先尝试简单的属性检查
                return hasattr(websocket, 'send_json')
        except Exception as e:
            logger.debug(f"检查WebSocket状态失败: {e}")
            # 如果检查失败，认为连接不活跃
            return False

    @classmethod
    async def send_message(cls, client_id: str, message_type: Literal['text', 'progress'], content: str):
        if client_id in cls._connections:
            try:
                websocket = cls._connections[client_id]
                # 检查连接状态
                if not cls.is_connection_active(client_id):
                    logger.warning(f"WebSocket连接 {client_id} 已断开，清理连接记录")
                    cls.remove_connection(client_id)
                    return False
                
                await websocket.send_json({
                    "type": message_type,
                    "message": content
                })
                return True
            except Exception as e:
                logger.error(f"发送消息失败: {str(e)}")
                # 发送失败时清理连接记录
                cls.remove_connection(client_id)
                return False
        else:
            logger.warning(f"WebSocket连接 {client_id} 不存在")
            return False


ws = GlobalWebSocket()
