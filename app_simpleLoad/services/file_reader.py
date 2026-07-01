"""文件读取服务 — 异步读取 TXT 载荷文件 + Excel 频次表

使用 asyncio + ThreadPoolExecutor 替代 ProcessPoolExecutor：
- 线程共享内存，无 pickle 序列化开销
- await 让出控制权，不阻塞事件循环
- WebSocket 进度推送保持流畅
"""

import os
import asyncio
from collections import Counter
from typing import List, Tuple, Sequence
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import polars as pl
import pandas as pd
from app_simpleLoad.core.config import ConversionConfig, FileResult, FileParseError
from app_simpleLoad.core.progress import ProgressReporter

# 模块级线程池（复用，避免反复创建销毁）
_thread_pool = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)


def normalize_load_file_name(value: object) -> str:
    """Normalize Excel/txt file identifiers for matching."""
    text = str(value).strip()
    stem, ext = os.path.splitext(text)
    if ext.lower() == ".txt":
        return stem.strip()
    return text


def _format_file_names(names: Sequence[str], limit: int = 10) -> str:
    sorted_names = sorted(names)
    shown = "、".join(sorted_names[:limit])
    remaining = len(sorted_names) - limit
    if remaining > 0:
        return f"{shown} 等 {len(sorted_names)} 个"
    return shown


def _duplicate_names(names: Sequence[str]) -> List[str]:
    counts = Counter(names)
    return sorted(name for name, count in counts.items() if count > 1)


def _validate_txt_file_mapping(
    expected_names: Sequence[str],
    actual_names: Sequence[str],
) -> None:
    """Validate that Excel frequency rows match actual txt files one-to-one."""
    duplicated_expected = _duplicate_names(expected_names)
    if duplicated_expected:
        raise ValueError(
            "频次表第一列存在重复文件名："
            f"{_format_file_names(duplicated_expected)}"
        )

    duplicated_actual = _duplicate_names(actual_names)
    if duplicated_actual:
        raise ValueError(
            "载荷文件夹中存在重复 txt 文件名（忽略目录和扩展名后）："
            f"{_format_file_names(duplicated_actual)}"
        )

    expected_set = set(expected_names)
    actual_set = set(actual_names)
    missing_names = sorted(expected_set - actual_set)
    extra_names = sorted(actual_set - expected_set)

    if not missing_names and not extra_names:
        return

    message_parts = [
        f"频次表包含 {len(expected_names)} 条记录，实际找到 {len(actual_names)} 个 txt 文件",
    ]
    if missing_names:
        message_parts.append(
            f"缺失 {len(missing_names)} 个 txt：{_format_file_names(missing_names)}"
        )
    if extra_names:
        message_parts.append(
            f"多余 {len(extra_names)} 个 txt：{_format_file_names(extra_names)}"
        )
    message_parts.append("请检查频次表第一列文件名与载荷文件夹中的 txt 文件名是否一致")

    raise ValueError(f"频次表与载荷文件不匹配：{'；'.join(message_parts)}")


def _parse_single_file(
    file_path: str,
    header: List[str],
    config: ConversionConfig,
    have_time: bool,
) -> FileResult:
    """同步解析单个 TXT 载荷文件（在线程池中执行）"""
    # 过滤掉占位符列
    valid_cols = [col for col in header if "占位符" not in col]
    valid_indices = [i for i, col in enumerate(header) if "占位符" not in col]

    # pandas 读取空格分隔 TXT（float32 省一半内存，精度对工程载荷足够）
    file_name = os.path.basename(file_path)
    try:
        df_pd = pd.read_csv(
            file_path,
            sep=r"\s+",
            header=config.title_row,
            names=valid_cols,
            dtype=np.float32,
            usecols=valid_indices,
        )
    except (ValueError, TypeError) as e:
        raise FileParseError(
            filename=file_name,
            reason=f"{e}，请检查 标题行 配置是否与文件列数匹配",
        ) from e
    except Exception as e:
        raise FileParseError(
            filename=file_name,
            reason=str(e),
        ) from e

    # 转为 Polars（后续用 Polars 做高性能计算）
    df = pl.from_pandas(df_pd)
    del df_pd

    # 单位转换
    moment_cols = ["Mx[KNm]", "My[KNm]", "Mz[KNm]"]
    force_cols = ["Fx[KN]", "Fy[KN]", "Fz[KN]"]

    df = df.with_columns(
        [
            (pl.col(col) / config.unit_moment).alias(col)
            for col in moment_cols
            if col in df.columns
        ]
        + [
            (pl.col(col) / config.unit_force).alias(col)
            for col in force_cols
            if col in df.columns
        ]
        + [
            (pl.col("speed[rpm]") * config.unit_speed).alias("speed[rpm]")
        ]
    )

    file_name_no_ext = normalize_load_file_name(os.path.basename(file_path))
    df = df.with_columns(pl.lit(file_name_no_ext).alias("文件名"))

    # 时间信息
    row_count = df.height
    if have_time:
        time_col = df["Time[s]"]
        var_time = float(time_col[-1] - time_col[0])
        var_interval = var_time / (row_count - 1)
        return FileResult(file_name_no_ext, df, row_count, var_time, var_interval)
    else:
        return FileResult(file_name_no_ext, df, row_count)


async def read_all_txt_files(
    folder_path: str,
    header: List[str],
    config: ConversionConfig,
    have_time: bool,
    expected_file_names: Sequence[str] | None = None,
    progress: ProgressReporter | None = None,
) -> Tuple[pl.DataFrame, List[FileResult]]:
    """异步并发读取文件夹下所有 TXT 载荷文件。

    Returns:
        df_all:       合并后的全量 DataFrame（含 '文件名' 列）
        file_results: 每个文件的解析结果列表
    """
    # 收集文件路径
    file_paths = []
    for root, _, files in os.walk(folder_path):
        txt_files = [f for f in files if f.lower().endswith(".txt")]
        file_paths.extend(os.path.join(root, f) for f in txt_files)
    file_paths.sort()

    total = len(file_paths)

    actual_file_names = [
        normalize_load_file_name(os.path.basename(path))
        for path in file_paths
    ]
    if expected_file_names is not None:
        normalized_expected_names = [
            normalize_load_file_name(name)
            for name in expected_file_names
        ]
        _validate_txt_file_mapping(normalized_expected_names, actual_file_names)

    if total == 0:
        raise ValueError(f"载荷文件夹中没有找到 txt 文件：{folder_path}")

    if progress:
        await progress.send_text(f"开始处理 {total} 个文件...")
        await progress.send_progress(0)

    # 通过线程池并发执行
    loop = asyncio.get_running_loop()
    processed = 0
    last_progress = 0.0
    frames: List[pl.DataFrame] = []
    file_results: List[FileResult] = []

    async def _run_one(fp: str):
        return await loop.run_in_executor(
            _thread_pool,
            _parse_single_file,
            fp,
            header,
            config,
            have_time,
        )

    tasks = [_run_one(fp) for fp in file_paths]

    for coro in asyncio.as_completed(tasks):
        result: FileResult = await coro
        frames.append(result.df)
        file_results.append(result)

        processed += 1
        current_progress = round((processed / total) * 100, 1)

        if progress and (current_progress - last_progress >= 5 or processed == total):
            await progress.update_smoothly(last_progress, current_progress, 0.5)
            await progress.send_text(f"已处理 {processed}/{total} 个文件")
            last_progress = current_progress

    # 合并所有 DataFrame
    df_all = pl.concat(frames)
    del frames

    return df_all, file_results


def read_freq_table(freq_table_path: str, have_time: bool) -> pl.DataFrame:
    """读取 Excel 频次表，返回 Polars DataFrame"""
    try:
        df_ref_pd = pd.read_excel(
            freq_table_path,
            names=["文件名", "全寿命发生次数", "仿真时间（s）"],
            header=0,
            dtype={"文件名": str},
        )
    except Exception as e:
        raise FileParseError(
            filename=os.path.basename(freq_table_path),
            reason=str(e),
        ) from e

    freq_file_name = os.path.basename(freq_table_path)
    raw_file_names = df_ref_pd["文件名"]
    if raw_file_names.isna().any():
        raise FileParseError(
            filename=freq_file_name,
            reason="频次表第一列存在空文件名",
        )

    normalized_file_names = raw_file_names.map(normalize_load_file_name)
    if (normalized_file_names == "").any():
        raise FileParseError(
            filename=freq_file_name,
            reason="频次表第一列存在空文件名",
        )

    duplicated_ref_names = _duplicate_names(normalized_file_names.tolist())
    if duplicated_ref_names:
        raise FileParseError(
            filename=freq_file_name,
            reason=f"频次表第一列存在重复文件名：{_format_file_names(duplicated_ref_names)}",
        )

    df_ref_pd["文件名"] = normalized_file_names

    df_ref = pl.from_pandas(df_ref_pd)
    del df_ref_pd

    if have_time:
        df_ref = df_ref.drop("仿真时间（s）")

    return df_ref
