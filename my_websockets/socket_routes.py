from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from .socket_manager import ConnectionManager
from loguru import logger
import json

router = APIRouter()

async def get_manager(websocket: WebSocket):
    return websocket.app.state.websocket_manager

@router.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket, 
    client_id: str, 
    manager: ConnectionManager = Depends(get_manager)
):
    try:
        await manager.connect(websocket, client_id)
        while True:
            try:
                data = await websocket.receive_text()
                logger.info(f"收到客户端 {client_id} 的消息: {data}")
                
                try:
                    message = json.loads(data)
                    if message.get("type") == "command":
                        await manager.handle_command(message, client_id)
                    elif message.get("type") == "broadcast":
                        await manager.broadcast(json.dumps({
                            "type": "broadcast",
                            "message": message.get("message")
                        }))
                except json.JSONDecodeError:
                    logger.error(f"无效的 JSON 消息: {data}")
                    
            except WebSocketDisconnect:
                manager.disconnect(client_id)
                break
            
    except Exception as e:
        logger.error(f"WebSocket 错误: {str(e)}")
        manager.disconnect(client_id)