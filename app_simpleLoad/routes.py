import json
import asyncio
from fastapi import APIRouter, Request
from loguru import logger
from typing import Dict

from app_simpleLoad.module.cal_simpleLoad import CalSimpleLoad
from utils import log_all_processes_memory
from my_websockets.global_ws import ws

router = APIRouter()


@router.post("/load_file")
async def load_file(request: Request, data_: Dict):
    """
    说明
    ---
    FUNC 加载文件
    """
    m = log_all_processes_memory()
    manager = request.app.state.websocket_manager
    instance: CalSimpleLoad | None = manager.cal_instance
    
    # 检查实例是否存在，如果不存在则创建新实例
    if instance is None:
        # 检查WebSocket连接状态，如果断开则等待重连
        if not ws.is_connection_active('simple_load'):
            logger.info("WebSocket连接断开，等待前端重连...")
            
            # 等待前端重连，最多等待10秒
            for i in range(20):  # 20次 * 0.5秒 = 10秒
                await asyncio.sleep(0.5)
                if ws.is_connection_active('simple_load'):
                    logger.info(f"WebSocket重连成功，等待了 {(i+1)*0.5} 秒")
                    break
            else:
                # 10秒后仍未重连成功
                return {
                    "message": "WebSocket连接已断开，请刷新页面重新连接", 
                    "status": "error",
                    "need_reconnect": True
                }
        
        instance = CalSimpleLoad()
        manager.cal_instance = instance
        logger.info("创建新的 CalSimpleLoad 实例，WebSocket连接正常")
    
    try:
        instance.setInit(
            result_folder_save_path=data_['file_path']['result_folder_save_path'],
            load_file_folder_path=data_['file_path']['load_file_folder_path'],
            freq_table_path=data_['file_path']['freq_table_path'],
            header=[item['name'] for item in data_['draggableElements']],
            conversion_factors=data_['conversion_factors'],
        )
    except ValueError as e:
        # 捕获header配置错误
        error_msg = str(e)
        logger.error(error_msg)
        return {"message": error_msg, "status": "error"}
    
    await instance.simple_Pre_processing()
    logger.warning(f'第一步内存增加: {log_all_processes_memory() - m} MB')
    return {"message": "读取文件完成", "status": "success"}


@router.post("/divide_interval")
async def simple_pre_processing(request: Request, data: Dict):
    """
    说明
    ---
    FUNC 划分区间
    """
    m = log_all_processes_memory()
    manager = request.app.state.websocket_manager
    instance: CalSimpleLoad | None = manager.cal_instance
    
    # 检查实例是否存在
    if instance is None:
        return {"message": "请先加载文件", "status": "error"}
    
    try:
        min_max = await instance.simple_load1(data.get('romax_origin', []))
        echarts_data = await instance.savePic()
        logger.warning(f'第二步内存增加: {log_all_processes_memory() - m} MB')
    except AttributeError as e:
        return {"message": f"请先加载文件", "status": "error"}
    return {
        "message": "划分区间完成",
        "min_max": min_max,
        "echarts_data": echarts_data,
        "status": "success",
    }


@router.post("/reduce_load")
async def simple_load(request: Request, data: Dict):
    """载荷简化接口"""
    manager = request.app.state.websocket_manager
    instance: CalSimpleLoad | None = manager.cal_instance
    
    # 检查实例是否存在
    if instance is None:
        return {"message": "请先加载文件", "status": "error"}
    
    msg = await instance.simple_load2(data['tableData'], data['romax_origin'])
    if isinstance(msg, dict):
        return {**msg, "status": "error"}
    return {"message": "载荷简化处理全部完成", "count": msg}