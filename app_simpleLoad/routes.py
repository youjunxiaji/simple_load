import json
from fastapi import APIRouter, Request
from loguru import logger
from typing import Dict

from app_simpleLoad.module.cal_simpleLoad import CalSimpleLoad
from utils import log_all_processes_memory

router = APIRouter()


@router.post("/load_file")
async def load_file(request: Request, data_: Dict):
    """
    说明
    ---
    FUNC 读取要处理的文件
    """
    m = log_all_processes_memory()
    manager = request.app.state.websocket_manager
    instance: CalSimpleLoad | None = manager.cal_instance
    
    # 检查实例是否存在，如果不存在则创建新实例
    if instance is None:
        instance = CalSimpleLoad()
        manager.cal_instance = instance
        logger.info("创建新的 CalSimpleLoad 实例，WebSocket连接正常")
    
    instance.setInit(
        result_folder_save_path=data_['file_path']['result_folder_save_path'],
        load_file_folder_path=data_['file_path']['load_file_folder_path'],
        freq_table_path=data_['file_path']['freq_table_path'],
        header=[item['name'] for item in data_['draggableElements']],
        conversion_factors=data_['conversion_factors'],

    )
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
