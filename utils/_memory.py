import psutil
import os
from multiprocessing import current_process
from loguru import logger


def log_memory_usage():
    """监控当前进程的内存使用情况"""
    process = psutil.Process(os.getpid())
    process_name = current_process().name
    memory_mb = process.memory_info().rss / 1024 / 1024
    logger.info(f"进程 {process_name}(PID:{os.getpid()}) 内存使用: {memory_mb:.2f} MB")


def log_all_processes_memory():
    """监控所有相关进程的内存使用情况"""
    current_process = psutil.Process(os.getpid())
    total_memory = 0
    
    # 获取当前进程及其所有子进程
    processes = [current_process] + current_process.children(recursive=True)
    
    for proc in processes:
        try:
            memory_mb = proc.memory_info().rss / 1024 / 1024
            total_memory += memory_mb
            logger.info(f"进程 PID:{proc.pid} 内存使用: {memory_mb:.2f} MB")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
            
    return round(total_memory, 2)