"""
载荷简化计算模块 — 编排层
调用 services 层完成文件读取，自身负责区间划分、直方图生成、载荷缩减。
"""

import os
import polars as pl
import numpy as np
from typing import List, Dict
from loguru import logger
import gc
import pandas as pd  # 仅用于 Excel 输出和 IntervalIndex

from app_simpleLoad.core.config import PathConfig, ConversionConfig
from app_simpleLoad.core.memory import log_memory, MemoryMonitor
from app_simpleLoad.core.progress import ProgressReporter
from app_simpleLoad.services.file_reader import read_all_txt_files, read_freq_table


# ─── 主类 ─────────────────────────────────────────────────────

class CalSimpleLoad:

    def __init__(self):
        self.progress = ProgressReporter()

    def setInit(
            self,
            paths: PathConfig,
            header: List[str],
            config: ConversionConfig,
    ):
        # 提前校验 header
        required_cols = {'Mx[KNm]', 'My[KNm]', 'Mz[KNm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]', 'speed[rpm]'}
        missing_cols = required_cols - set(header)
        if missing_cols:
            raise ValueError(f"标题配置错误：缺少必需的列 {list(missing_cols)}")

        self.paths = paths
        self.header = header
        self.config = config

        self.df_all: pl.DataFrame | None = None
        self.df_dest = None
        self.df_ref: pl.DataFrame | None = None

    # ─── 步骤一：加载与预处理 ──────────────────────────────────

    async def simple_Pre_processing(self):
        """加载文件 — 异步线程池版本"""
        memory_start = log_memory("预处理-开始")
        monitor = MemoryMonitor()
        monitor.start("预处理-峰值监控")

        # 释放旧数据
        self.df_all = None
        self.df_dest = None
        self.df_ref = None
        gc.collect()
        log_memory("预处理-GC后", memory_start)

        have_time = 'Time[s]' in self.header

        # 读取 Excel 频次表
        self.df_ref = read_freq_table(self.paths.freq_table_path, have_time)

        # 异步读取所有 TXT 文件（线程池并发，不阻塞事件循环）
        self.df_all, file_results = await read_all_txt_files(
            self.paths.load_file_folder_path,
            self.header,
            self.config,
            have_time,
            progress=self.progress,
        )
        log_memory("预处理-文件读取完成", memory_start)

        # 构建参考表补充信息
        if have_time:
            ref_data = {
                '文件名': [r.name for r in file_results],
                '载荷行数': [r.row_count for r in file_results],
                '仿真时间（s）': [r.sim_time for r in file_results],
                '采样间隔（s）': [r.sample_interval for r in file_results],
            }
        else:
            ref_data = {
                '文件名': [r.name for r in file_results],
                '载荷行数': [r.row_count for r in file_results],
            }
        df_ref1 = pl.DataFrame(ref_data)

        # 合并参考表
        self.df_ref = self.df_ref.join(df_ref1, on='文件名', how='left')

        # 如果没有时间列，计算采样间隔
        if not have_time:
            self.df_ref = self.df_ref.with_columns(
                (pl.col('仿真时间（s）') / (pl.col('载荷行数') - 1)).alias('采样间隔（s）')
            )

        # 计算工况占比
        total_weighted_time = (self.df_ref['仿真时间（s）'] * self.df_ref['全寿命发生次数']).sum()
        self.df_ref = self.df_ref.with_columns(
            (pl.col('仿真时间（s）') * pl.col('全寿命发生次数') / total_weighted_time).alias('工况占比')
        )

        log_memory("预处理-完成", memory_start)
        monitor.stop()

    # ─── 步骤二：划分区间 ─────────────────────────────────────

    async def simple_load1(self, romax_origin):
        """划分区间 — Polars 版本"""
        if self.df_all is None:
            logger.error("df_all is None")
            return

        df_des = self.df_all.describe()

        min_row = df_des.filter(pl.col('statistic') == 'min')
        max_row = df_des.filter(pl.col('statistic') == 'max')

        moment_cols = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]']
        force_cols = ['Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        all_cols = moment_cols + force_cols

        dest_data = {
            'column': all_cols,
            'min': [float(min_row[col][0]) for col in all_cols],
            'max': [float(max_row[col][0]) for col in all_cols]
        }
        self.df_dest = pl.DataFrame(dest_data)

        def create_bins(min_val, max_val):
            if min_val * max_val < 0:
                return np.concatenate([
                    np.linspace(np.floor(min_val / 100) * 100, 0, 100, endpoint=False, dtype=int),
                    np.linspace(0, np.ceil(max_val / 100) * 100, 100, endpoint=True, dtype=int)
                ])
            return np.linspace(
                np.floor(min_val / 100) * 100,
                np.ceil(max_val / 100) * 100,
                200,
                endpoint=True,
                dtype=int
            )

        self.max_min = {}
        for col in all_cols:
            min_val = float(min_row[col][0])
            max_val = float(max_row[col][0])
            self.max_min[col] = create_bins(min_val, max_val)

        self.df_dest = self.df_dest.with_columns([
            pl.col('min').floor(),
            pl.col('max').ceil()
        ])

        return self.df_dest.to_pandas().set_index('column').to_json()

    # ─── 步骤二（续）：直方图数据生成 ────────────────────────────

    async def savePic(self):
        """生成加权直方图数据 — 用 partition_by 替代 df_dic 遍历"""
        columns = ["Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]", "Mz[KNm]"]

        if self.df_ref is None or self.df_all is None:
            return {"message": "请先加载文件", "status": "error"}

        # 构建权重映射
        weights = {}
        for row in self.df_ref.iter_rows(named=True):
            weights[row['文件名']] = row['工况占比']

        result_dict = {}

        await self.progress.send_text("开始生成图表数据...")
        await self.progress.send_progress(0)

        # 按文件名分组（一次分组，多次使用）
        groups = self.df_all.partition_by('文件名', as_dict=True)

        for idx, column in enumerate(columns):
            weighted_hist = np.zeros(len(self.max_min[column]) - 1)

            for key, group_df in groups.items():
                # partition_by 的 key 可能是元组 ('文件名',) 或纯字符串
                file_name = key[0] if isinstance(key, tuple) else key
                col_data = group_df[column].to_numpy()
                indices = np.digitize(col_data, self.max_min[column]) - 1
                counts = np.bincount(indices, minlength=len(self.max_min[column]) - 1)
                hist = counts / group_df.height
                weighted_hist += hist * weights[file_name]

            # 创建区间索引
            intervals = pd.IntervalIndex.from_arrays(
                self.max_min[column][:-1],
                self.max_min[column][1:],
                closed='right'
            )

            mx_ser = pd.Series(weighted_hist, index=intervals)
            filtered_ser = mx_ser[mx_ser >= 1e-4]
            result_dict[column] = filtered_ser.to_json()

            current_progress = round(((idx + 1) / len(columns)) * 100, 1)
            await self.progress.update_smoothly(idx * 100 / len(columns), current_progress, 0.3)
            await self.progress.send_text(f"已处理 {column} 列数据")

        del groups  # 释放分组引用

        await self.progress.send_text("图表数据生成完成")
        return result_dict

    # ─── 步骤三：载荷缩减 ─────────────────────────────────────

    async def simple_load2(self, table_data, romax_origin: Dict):
        """载荷缩减 — Polars 版本"""
        if self.df_all is None or self.df_ref is None:
            return {"message": "请先加载文件", "status": "error"}

        memory_start = log_memory("载荷缩减-开始")
        monitor = MemoryMonitor()
        monitor.start("载荷缩减-峰值监控")

        await self.progress.send_text("开始载荷缩减处理...")
        await self.progress.update_smoothly(0, 10, 0.5)

        # 解析用户定义的分区边界
        lists = [
            [float(value) for value in row.values() if value != '']
            for row in table_data
        ]

        # 根据 romax_origin 确定要排除的轴
        z_corresponds_to = romax_origin[2]['origin'].replace("-", "")

        all_components = [
            ('fx', 'Fx[KN]', 'x'),
            ('fy', 'Fy[KN]', 'y'),
            ('fz', 'Fz[KN]', 'z'),
            ('mx', 'Mx[KNm]', 'x'),
            ('my', 'My[KNm]', 'y'),
            ('mz', 'Mz[KNm]', 'z')
        ]

        # 排除 z 对应轴的力矩分量
        selected_components = []
        for comp_name, col_name, axis in all_components:
            if comp_name.startswith('m') and axis == z_corresponds_to:
                continue
            selected_components.append((comp_name, col_name, axis))

        # 构建标签映射
        label_mappings = []
        for i, (comp_name, col_name, axis) in enumerate(selected_components):
            if i < len(lists):
                label_name = f"{comp_name}_label"
                label_mappings.append((label_name, col_name, lists[i]))

        self._label_prefixes = {}

        # 创建标签列
        new_columns = []
        for label_name, col_name, bins in label_mappings:
            if not all(x <= y for x, y in zip(bins[:-1], bins[1:])):
                return {"message": f"{col_name}的区间值必须是单调递增的", "status": "error"}

            col_data = self.df_all[col_name].to_numpy()
            indices = np.digitize(col_data, bins) - 1
            max_possible_index = len(bins)
            indices = np.clip(indices, 0, max_possible_index - 1)

            new_columns.append(pl.Series(label_name, indices.astype(np.int16)))
            self._label_prefixes[label_name] = label_name[0:2]

        self.df_all = self.df_all.with_columns(new_columns)

        log_memory("载荷缩减-标签创建后", memory_start)

        # Polars join
        log_memory("载荷缩减-join前", memory_start)

        df_final = self.df_all.join(
            self.df_ref,
            on='文件名',
            how='left'
        )

        # 立刻释放 df_all（join 后已不需要，避免与 df_final 同时占用内存）
        self.df_all = None
        gc.collect()

        log_memory("载荷缩减-join后(df_all已释放)", memory_start)

        # 向量化计算
        df_final = df_final.with_columns([
            pl.col('speed[rpm]').abs().alias('speed[rpm]'),
            (pl.col('采样间隔（s）') * pl.col('全寿命发生次数')).alias('interval_life')
        ])

        df_final = df_final.with_columns([
            (pl.col('interval_life') * pl.col('speed[rpm]')).alias('格子转速'),
            pl.col('interval_life').alias('格子时间')
        ])

        # 幂变换
        value_list = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        translate_factor = self.config.translate_factor

        await self.progress.send_text("正在进行载荷转换计算...")

        def power_transform(col_name):
            return pl.when(pl.col(col_name) < 0).then(
                -pl.col(col_name).abs().pow(translate_factor)
            ).otherwise(
                pl.col(col_name).pow(translate_factor)
            ).alias(col_name)

        df_final = df_final.with_columns([
            power_transform(col) for col in value_list
        ])

        log_memory("载荷缩减-载荷转换后", memory_start)

        await self.progress.send_text("载荷缩减完成")
        await self.progress.send_text("正在转换数据")

        # 动态定义列
        dynamic_load_cols = [col_name for _, col_name, _ in label_mappings if 'KN' in col_name]
        speed_cols = ['speed[rpm]'] + dynamic_load_cols
        processed_cols = ['处理后_speed[rpm]'] + [f'处理后_{col}' for col in dynamic_load_cols]

        df_final = df_final.with_columns([
            (pl.col(col) * pl.col('格子转速')).alias(f'处理后_{col}')
            for col in speed_cols
        ])

        # 聚合
        index_cols = [label_name for label_name, _, _ in label_mappings]
        value_cols = processed_cols + ['格子转速', '格子时间']

        log_memory("载荷缩减-groupby前", memory_start)

        df_pivot = df_final.group_by(index_cols).agg([
            pl.col(col).sum() for col in value_cols
        ])

        # 释放 df_final（df_all 已在 join 后释放）
        del df_final
        gc.collect()
        log_memory("载荷缩减-groupby后(释放df_final)", memory_start)

        await self.progress.send_text("数据转换完成")
        await self.progress.update_smoothly(10, 40, 1.0)

        # 过滤零值行
        filter_expr = pl.lit(True)
        for col in df_pivot.columns:
            if col not in index_cols:
                filter_expr = filter_expr & (pl.col(col) != 0)
        df_pivot = df_pivot.filter(filter_expr)
        count_ = df_pivot.height

        # 时间占比
        total_grid_speed = df_pivot['格子转速'].sum()
        df_pivot = df_pivot.with_columns(
            (pl.col('格子转速') / total_grid_speed).alias('时间占比')
        )

        df_pivot = df_pivot.filter(pl.col('时间占比') > self.config.tol)

        # 速度和载荷列
        for col in speed_cols:
            df_pivot = df_pivot.with_columns(
                (pl.col(f'处理后_{col}') / pl.col('格子转速')).alias(col)
            )

        await self.progress.send_text("正在处理载荷数据...")
        await self.progress.update_smoothly(40, 70, 1.0)

        # 逆幂变换
        inv_factor = 1 / translate_factor

        def inv_power_transform(col_name):
            return pl.when(pl.col(col_name) < 0).then(
                -pl.col(col_name).abs().pow(inv_factor)
            ).when(pl.col(col_name) > 0).then(
                pl.col(col_name).pow(inv_factor)
            ).otherwise(0.0).alias(col_name)

        df_pivot = df_pivot.with_columns([
            inv_power_transform(col) for col in dynamic_load_cols
        ])

        # 计算 time(h)
        total_time = (self.df_ref['仿真时间（s）'] * self.df_ref['全寿命发生次数']).sum()
        df_pivot = df_pivot.with_columns(
            (pl.col('时间占比') * total_time / 3600).alias('time(h)')
        )

        # 重排列
        final_cols = ['time(h)', 'speed[rpm]'] + dynamic_load_cols + ['时间占比', '格子转速'] + index_cols
        df_pivot = df_pivot.select([col for col in final_cols if col in df_pivot.columns])

        # 工况列和标签转换
        df_pivot = df_pivot.with_row_index('_row_idx')
        df_pivot = df_pivot.with_columns(
            (pl.lit('loc') + (pl.col('_row_idx') + 1).cast(pl.Utf8).str.pad_start(3, '0')).alias('工况')
        )

        for label_name in index_cols:
            prefix = self._label_prefixes[label_name]
            df_pivot = df_pivot.with_columns(
                (pl.lit(prefix) + (pl.col(label_name) + 1).cast(pl.Utf8)).alias(label_name)
            )

        df_pivot = df_pivot.drop('_row_idx')

        await self.progress.send_text("正在保存Excel文件...")
        await self.progress.update_smoothly(70, 95, 1.0)

        # 转为 pandas 输出 Excel
        df_pivot_pd = df_pivot.to_pandas()
        df_pivot_pd = df_pivot_pd.set_index(index_cols)

        excel_name = os.path.basename(self.paths.result_folder_save_path)
        df_pivot_pd.to_excel(f'{self.paths.result_folder_save_path}/Load_Reduction_GL-{excel_name}.xlsx')

        # Romax 格式输出
        df_pivot_Romax = pd.DataFrame()
        df_pivot_Romax['工况'] = df_pivot_pd['工况'].values
        df_pivot_Romax['time(h)'] = df_pivot_pd['time(h)'].values
        df_pivot_Romax['温度(C)'] = self.config.temperature
        df_pivot_Romax['speed[rpm]'] = df_pivot_pd['speed[rpm]'].values

        cols_ = ['Fx[KN]', 'Fy[KN]', 'Fz[KN]', 'Mx[KNm]', 'My[KNm]']
        for col in cols_:
            condition_ = [x['origin'] for x in romax_origin if x['romax'] == col[1]][0]
            source_col = col.replace(col[1], condition_).replace("-", "")
            if source_col in df_pivot_pd.columns:
                if "-" in condition_:
                    df_pivot_Romax[col] = -1.0 * df_pivot_pd[source_col].to_numpy()
                else:
                    df_pivot_Romax[col] = df_pivot_pd[source_col].to_numpy()
            else:
                df_pivot_Romax[col] = 0.0

        df_pivot_Romax_T = df_pivot_Romax[['工况'] + cols_].T
        with pd.ExcelWriter(f'{self.paths.result_folder_save_path}/Load_Reduction_Romax-{excel_name}.xlsx') as writer:
            df_pivot_Romax[['工况', 'time(h)', '温度(C)', 'speed[rpm]']].to_excel(writer, sheet_name='工况表格定义', index=False)
            df_pivot_Romax_T.to_excel(writer, sheet_name='载荷', header=False)
            df_pivot_Romax.to_excel(writer, sheet_name='未转置')
            df_pivot_Romax_T.to_excel(writer, sheet_name='已转置')

        await self.progress.send_text("Excel 已保存")
        await self.progress.update_smoothly(95, 100, 0.5)
        await self.progress.send_text("载荷缩减处理全部完成！")

        # 释放内存
        del df_pivot, df_pivot_pd, df_pivot_Romax, df_pivot_Romax_T
        gc.collect()

        log_memory("载荷缩减-全部完成(已释放)", memory_start)
        monitor.stop()
        return count_
