"""
Polars 版本的载荷简化计算模块
使用 Polars 替代 Pandas 以获得更好的内存效率和性能
"""
from PySide6.QtCore import *
import os
import polars as pl
import numpy as np
import fnmatch
from typing import List, Dict
from my_websockets.global_ws import ws
from loguru import logger
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
import gc
import asyncio
import psutil
import pandas as pd  # 仅用于 Excel 输出和部分兼容操作


def get_memory_usage():
    """获取当前进程内存使用情况 (MB)"""
    process = psutil.Process()
    memory_info = process.memory_info()
    return {
        'rss': memory_info.rss / 1024 / 1024,  # 实际物理内存
        'vms': memory_info.vms / 1024 / 1024,  # 虚拟内存
    }


def log_memory(stage: str, memory_start: dict | None = None):
    """记录内存使用情况"""
    current = get_memory_usage()
    if memory_start:
        delta_rss = current['rss'] - memory_start['rss']
        logger.info(f"📊 [{stage}] 内存: {current['rss']:.1f}MB (变化: {delta_rss:+.1f}MB)")
    else:
        logger.info(f"📊 [{stage}] 内存: {current['rss']:.1f}MB")
    return current


class CalSimpleLoad:

    async def _update_progress_smoothly(self, start_progress: float, end_progress: float, duration: float = 1.0):
        """平滑更新进度条"""
        steps = int(duration * 10)  # 每0.1秒更新一次
        if steps <= 0:
            steps = 1

        step_size = (end_progress - start_progress) / steps
        current_progress = start_progress

        for i in range(steps):
            current_progress += step_size
            success = await ws.send_message('simple_load', 'progress', f"{round(current_progress, 1)}")
            if not success:
                logger.warning("WebSocket连接断开，停止进度更新")
                break
            await asyncio.sleep(0.1)

        # 确保最终进度准确
        await ws.send_message('simple_load', 'progress', f"{round(end_progress, 1)}")

    def setInit(
            self,
            result_folder_save_path: str,
            load_file_folder_path: str,
            freq_table_path: str,
            header: List[str],
            conversion_factors: Dict[str, float],
    ):
        # 提前校验header，避免后续处理浪费资源
        required_cols = {'Mx[KNm]', 'My[KNm]', 'Mz[KNm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]', 'speed[rpm]'}

        # 检查是否缺少必需列
        missing_cols = required_cols - set(header)
        if missing_cols:
            raise ValueError(f"标题配置错误：缺少必需的列 {list(missing_cols)}")

        self.result_folder_save_path = result_folder_save_path  # 结果文件夹保存路径
        self.load_file_folder_path = load_file_folder_path  # 时序载荷文件夹
        self.freq_table_path = freq_table_path  # 频次表位置
        self.header = header  # 标题行
        self.conversion_factors = conversion_factors  # 转换系数表单
        #
        self.df_all: pl.DataFrame | None = None
        self.df_dest = None
        self.df_dic: Dict[str, pl.DataFrame] | None = None
        self.df_ref: pl.DataFrame | None = None

    def _process_single_file_sync(self, args):
        """同步处理单个文件的函数 - Polars 版本"""
        file_path, header, conversion_factors, have_time = args
        
        # 过滤掉占位符列
        valid_cols = [col for col in header if '占位符' not in col]
        
        # 使用 pandas 读取（因为 polars 对空格分隔支持不好）然后转换
        import pandas as pd_local
        df_pd = pd_local.read_csv(
            file_path,
            sep=r'\s+',
            header=conversion_factors['title_row'],
            names=header,
            dtype=float,
            usecols=range(len(header))
        )
        
        # 只保留有效列
        df_pd = df_pd.loc[:, ~df_pd.columns.str.contains('占位符')]
        
        # 转换为 Polars（零拷贝如果数据类型兼容）
        df = pl.from_pandas(df_pd)
        del df_pd  # 立即释放 pandas DataFrame
        
        # 单位转换 - Polars 表达式
        moment_cols = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]']
        force_cols = ['Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        
        df = df.with_columns([
            (pl.col(col) / conversion_factors['unit_moment']).alias(col)
            for col in moment_cols if col in df.columns
        ] + [
            (pl.col(col) / conversion_factors['unit_force']).alias(col)
            for col in force_cols if col in df.columns
        ] + [
            (pl.col('speed[rpm]') * conversion_factors['unit_speed']).alias('speed[rpm]')
        ])
        
        file_name = os.path.basename(file_path)[:-4]
        
        # 添加文件名列
        df = df.with_columns(pl.lit(file_name).alias('文件名'))
        
        # 计算时间间隔
        row_count = df.height
        if have_time:
            time_col = df['Time[s]']
            var_time = float(time_col[-1] - time_col[0])
            var_interval = var_time / (row_count - 1)
            return file_name, df, [row_count, var_time, var_interval]
        else:
            return file_name, df, [row_count]

    async def simple_Pre_processing(self):
        """
        说明
        ---
        FUNC 加载文件 - Polars 版本
        """
        # 内存监控 - 开始
        memory_start = log_memory("预处理-开始")
        
        # 释放内存
        self.df_all = None
        self.df_dest = None
        self.df_dic = None
        self.df_ref = None
        gc.collect()
        log_memory("预处理-GC后", memory_start)

        # 处理Excel - 使用 Polars 读取
        df_ref_pd = pd.read_excel(
            self.freq_table_path,
            names=['文件名', '全寿命发生次数', '仿真时间（s）'],
            header=0,
            dtype={'文件名': str}
        )
        self.df_ref = pl.from_pandas(df_ref_pd)
        del df_ref_pd
        
        have_time = 'Time[s]' in self.header
        if have_time:
            self.df_ref = self.df_ref.drop('仿真时间（s）')
            
        # 获取所有文件路径
        file_paths = []
        for root, _, files in os.walk(self.load_file_folder_path):
            txt_files = fnmatch.filter(files, "*.txt")
            file_paths.extend([os.path.join(root, f) for f in txt_files])

        total_files = len(file_paths)
        processed_files = 0
        last_progress = 0.0

        # 多进程处理
        await ws.send_message('simple_load', 'text', f"开始处理 {total_files} 个文件...")
        await ws.send_message('simple_load', 'progress', "0")

        with ProcessPoolExecutor(max_workers=mp.cpu_count()) as executor:
            # 提交所有任务
            future_to_file = {
                executor.submit(self._process_single_file_sync, (file_path, self.header, self.conversion_factors, have_time)): file_path
                for file_path in file_paths
            }

            # 处理完成的任务
            results = []
            for future in as_completed(future_to_file):
                processed_files += 1
                current_progress = round((processed_files / total_files) * 100, 1)

                # 如果进度变化超过5%或者是最后一个文件，才更新进度条
                if current_progress - last_progress >= 5 or processed_files == total_files:
                    # 平滑更新进度条
                    await self._update_progress_smoothly(last_progress, current_progress, 0.5)
                    last_progress = current_progress

                    await ws.send_message('simple_load', 'text', f"已处理 {processed_files}/{total_files} 个文件")

                result = future.result()
                results.append(result)

        # 处理结果
        log_memory("预处理-多进程完成", memory_start)
        self.df_dic = {r[0]: r[1] for r in results}
        df_dic1 = {r[0]: r[2] for r in results}
        log_memory("预处理-df_dic创建后", memory_start)

        # 创建参考表
        if have_time:
            ref_data = {
                '文件名': list(df_dic1.keys()),
                '载荷行数': [v[0] for v in df_dic1.values()],
                '仿真时间（s）': [v[1] for v in df_dic1.values()],
                '采样间隔（s）': [v[2] for v in df_dic1.values()]
            }
        else:
            ref_data = {
                '文件名': list(df_dic1.keys()),
                '载荷行数': [v[0] for v in df_dic1.values()]
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
        
        log_memory("预处理-concat前", memory_start)
        
        # Polars concat - 比 pandas 更高效
        self.df_all = pl.concat(list(self.df_dic.values()))
        
        log_memory("预处理-concat后(df_all创建)", memory_start)
        logger.info(f"📊 df_all 形状: {self.df_all.shape}, df_dic 文件数: {len(self.df_dic)}")

    async def simple_load1(self, romax_origin):
        """
        说明
        ---
        FUNC 划分区间 - Polars 版本
        """
        # 读取从Mx[Nm]~Fz[N]的最大最小值
        if self.df_all is None:
            logger.error("df_all is None")
            return
            
        # Polars describe
        df_des = self.df_all.describe()
        
        # 提取 min 和 max 行
        min_row = df_des.filter(pl.col('statistic') == 'min')
        max_row = df_des.filter(pl.col('statistic') == 'max')
        
        # 定义要处理的列
        moment_cols = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]']
        force_cols = ['Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        all_cols = moment_cols + force_cols
        
        # 创建 df_dest
        dest_data = {
            'column': all_cols,
            'min': [float(min_row[col][0]) for col in all_cols],
            'max': [float(max_row[col][0]) for col in all_cols]
        }
        self.df_dest = pl.DataFrame(dest_data)

        # 统一处理函数
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

        # 使用字典推导式一次性创建所有区间
        self.max_min = {}
        for col in all_cols:
            min_val = float(min_row[col][0])
            max_val = float(max_row[col][0])
            self.max_min[col] = create_bins(min_val, max_val)
        
        # 更新 df_dest - 使用 Polars 原生表达式
        self.df_dest = self.df_dest.with_columns([
            pl.col('min').floor(),
            pl.col('max').ceil()
        ])
        
        return self.df_dest.to_pandas().set_index('column').to_json()

    async def savePic(self):
        """使用 Polars 优化的图表数据生成"""

        columns = ["Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]", "Mz[KNm]"]
        if self.df_ref is None or self.df_dic is None:
            return {"message": "请先加载文件", "status": "error"}
        
        # 获取工况占比
        weights = {}
        for row in self.df_ref.iter_rows(named=True):
            weights[row['文件名']] = row['工况占比']
        
        result_dict = {}

        await ws.send_message('simple_load', 'text', "开始生成图表数据...")
        await ws.send_message('simple_load', 'progress', "0")

        for idx, column in enumerate(columns):
            weighted_hist = np.zeros(len(self.max_min[column]) - 1)

            for file_name, df in self.df_dic.items():
                # 使用numpy的digitize来模拟pandas的cut
                col_data = df[column].to_numpy()
                indices = np.digitize(col_data, self.max_min[column]) - 1
                # 计算每个区间的计数
                counts = np.bincount(indices, minlength=len(self.max_min[column]) - 1)
                # 归一化
                hist = counts / len(df)
                weighted_hist += hist * weights[file_name]

            # 创建与pandas相同的区间索引（用于JSON输出）
            intervals = pd.IntervalIndex.from_arrays(
                self.max_min[column][:-1],
                self.max_min[column][1:],
                closed='right'
            )

            # 转换为Series并保持相同的索引结构
            mx_ser = pd.Series(weighted_hist, index=intervals)
            filtered_ser = mx_ser[mx_ser >= 1e-4]
            result_dict[column] = filtered_ser.to_json()

            # 平滑更新进度
            current_progress = round(((idx + 1) / len(columns)) * 100, 1)
            await self._update_progress_smoothly(idx * 100 / len(columns), current_progress, 0.3)
            await ws.send_message('simple_load', 'text', f"已处理 {column} 列数据")

        await ws.send_message('simple_load', 'text', "图表数据生成完成")
        return result_dict

    async def simple_load2(self, table_data, romax_origin: Dict):
        """
        FUNC 载荷缩减 - Polars 版本
        """
        if self.df_all is None or self.df_ref is None:
            return {"message": "请先加载文件", "status": "error"}
        
        # 内存监控 - 开始
        memory_start = log_memory("载荷缩减-开始")
        
        await ws.send_message('simple_load', 'text', "开始载荷缩减处理...")
        await self._update_progress_smoothly(0, 10, 0.5)
        
        # 优化数据提取
        lists = [
            [float(value) for value in row.values() if value != '']
            for row in table_data
        ]

        # 动态规划方式：根据 romax_origin 确定要排除的轴
        z_corresponds_to = romax_origin[2]['origin'].replace("-", "")

        # 定义所有可能的分量和其对应的轴
        all_components = [
            ('fx', 'Fx[KN]', 'x'),
            ('fy', 'Fy[KN]', 'y'),
            ('fz', 'Fz[KN]', 'z'),
            ('mx', 'Mx[KNm]', 'x'),
            ('my', 'My[KNm]', 'y'),
            ('mz', 'Mz[KNm]', 'z')
        ]

        # 动态筛选：排除 z 对应轴的力矩分量
        selected_components = []
        for comp_name, col_name, axis in all_components:
            if comp_name.startswith('m') and axis == z_corresponds_to:
                continue
            selected_components.append((comp_name, col_name, axis))

        # 动态构建标签映射和数据分配
        label_mappings = []
        for i, (comp_name, col_name, axis) in enumerate(selected_components):
            if i < len(lists):
                label_name = f"{comp_name}_label"
                label_mappings.append((label_name, col_name, lists[i]))
        
        # 存储标签前缀
        self._label_prefixes = {}
        
        # 创建标签列 - Polars 方式
        new_columns = []
        for label_name, col_name, bins in label_mappings:
            # 检查是否单调
            if not all(x <= y for x, y in zip(bins[:-1], bins[1:])):
                return {"message": f"{col_name}的区间值必须是单调递增的", "status": "error"}
            
            # 使用 Polars 的 cut 功能或手动 digitize
            col_data = self.df_all[col_name].to_numpy()
            indices = np.digitize(col_data, bins) - 1
            max_possible_index = len(bins)
            indices = np.clip(indices, 0, max_possible_index - 1)
            
            # 添加为新列（使用 int16 节省内存）
            new_columns.append(pl.Series(label_name, indices.astype(np.int16)))
            self._label_prefixes[label_name] = label_name[0:2]
        
        # 一次性添加所有标签列
        self.df_all = self.df_all.with_columns(new_columns)
        
        log_memory("载荷缩减-标签创建后", memory_start)

        # 释放 df_dic
        if self.df_dic is not None:
            logger.info(f"📊 释放 df_dic，包含 {len(self.df_dic)} 个文件")
            self.df_dic = None
            gc.collect()
            log_memory("载荷缩减-释放df_dic后", memory_start)

        # Polars join 代替 pandas merge
        log_memory("载荷缩减-join前", memory_start)
        
        # 使用 Polars join（比 pandas merge 更高效）
        df_final = self.df_all.join(
            self.df_ref,
            on='文件名',
            how='left'
        )
        
        log_memory("载荷缩减-join后", memory_start)

        # 向量化计算
        df_final = df_final.with_columns([
            pl.col('speed[rpm]').abs().alias('speed[rpm]'),
            (pl.col('采样间隔（s）') * pl.col('全寿命发生次数')).alias('interval_life')
        ])
        
        df_final = df_final.with_columns([
            (pl.col('interval_life') * pl.col('speed[rpm]')).alias('格子转速'),
            pl.col('interval_life').alias('格子时间')
        ])

        # 处理载荷数据
        value_list = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        translate_factor = self.conversion_factors['translate_factor']

        await ws.send_message('simple_load', 'text', "正在进行载荷转换计算...")

        # Polars 向量化幂运算
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

        await ws.send_message('simple_load', 'text', "载荷缩减完成")
        await ws.send_message('simple_load', 'text', "正在转换数据")

        # 动态定义需要处理的列
        dynamic_load_cols = [col_name for _, col_name, _ in label_mappings if 'KN' in col_name]
        speed_cols = ['speed[rpm]'] + dynamic_load_cols
        processed_cols = ['处理后_speed[rpm]'] + [f'处理后_{col}' for col in dynamic_load_cols]

        # 计算处理后的列
        df_final = df_final.with_columns([
            (pl.col(col) * pl.col('格子转速')).alias(f'处理后_{col}')
            for col in speed_cols
        ])
        
        # 聚合操作（替代 pivot_table）
        index_cols = [label_name for label_name, _, _ in label_mappings]
        value_cols = processed_cols + ['格子转速', '格子时间']

        log_memory("载荷缩减-groupby前", memory_start)
        
        # Polars group_by（比 pandas pivot_table 更高效）
        df_pivot = df_final.group_by(index_cols).agg([
            pl.col(col).sum() for col in value_cols
        ])
        
        # 释放 df_final 和 df_all
        self.df_all = None
        del df_final
        gc.collect()
        log_memory("载荷缩减-groupby后(释放df_all)", memory_start)
        
        await ws.send_message('simple_load', 'text', "数据转换完成")
        await self._update_progress_smoothly(10, 40, 1.0)

        # 过滤零值行
        filter_expr = pl.lit(True)
        for col in df_pivot.columns:
            if col not in index_cols:
                filter_expr = filter_expr & (pl.col(col) != 0)
        df_pivot = df_pivot.filter(filter_expr)
        count_ = df_pivot.height

        # 计算时间占比
        total_grid_speed = df_pivot['格子转速'].sum()
        df_pivot = df_pivot.with_columns(
            (pl.col('格子转速') / total_grid_speed).alias('时间占比')
        )
        
        # 过滤小于容差的行
        df_pivot = df_pivot.filter(pl.col('时间占比') > self.conversion_factors['tol'])

        # 计算速度和载荷列
        for col in speed_cols:
            df_pivot = df_pivot.with_columns(
                (pl.col(f'处理后_{col}') / pl.col('格子转速')).alias(col)
            )

        await ws.send_message('simple_load', 'text', "正在处理载荷数据...")
        await self._update_progress_smoothly(40, 70, 1.0)

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

        # 添加工况列和转换标签
        df_pivot = df_pivot.with_row_index('_row_idx')
        df_pivot = df_pivot.with_columns(
            (pl.lit('loc') + (pl.col('_row_idx') + 1).cast(pl.Utf8).str.pad_start(3, '0')).alias('工况')
        )
        
        # 将整数标签转为字符串
        for label_name in index_cols:
            prefix = self._label_prefixes[label_name]
            df_pivot = df_pivot.with_columns(
                (pl.lit(prefix) + (pl.col(label_name) + 1).cast(pl.Utf8)).alias(label_name)
            )
        
        df_pivot = df_pivot.drop('_row_idx')

        await ws.send_message('simple_load', 'text', "正在保存Excel文件...")
        await self._update_progress_smoothly(70, 95, 1.0)

        # 转换为 pandas 以便输出 Excel
        df_pivot_pd = df_pivot.to_pandas()
        df_pivot_pd = df_pivot_pd.set_index(index_cols)
        
        excel_name = os.path.basename(self.result_folder_save_path)
        df_pivot_pd.to_excel(f'{self.result_folder_save_path}/Load_Reduction_GL-{excel_name}.xlsx')
        
        # 创建 Romax 格式输出
        df_pivot_Romax = pd.DataFrame()
        df_pivot_Romax['工况'] = df_pivot_pd['工况'].values
        df_pivot_Romax['time(h)'] = df_pivot_pd['time(h)'].values
        df_pivot_Romax['温度(C)'] = self.conversion_factors['temperature']
        df_pivot_Romax['speed[rpm]'] = df_pivot_pd['speed[rpm]'].values

        # 载荷关系转换
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

        df_pivot_Romax_T = df_pivot_Romax[['工况']+cols_].T
        with pd.ExcelWriter(f'{self.result_folder_save_path}/Load_Reduction_Romax-{excel_name}.xlsx') as writer:
            df_pivot_Romax[['工况', 'time(h)', '温度(C)', 'speed[rpm]']].to_excel(writer, sheet_name='工况表格定义', index=False)
            df_pivot_Romax_T.to_excel(writer, sheet_name='载荷', header=False)
            df_pivot_Romax.to_excel(writer, sheet_name='未转置')
            df_pivot_Romax_T.to_excel(writer, sheet_name='已转置')
            
        await ws.send_message('simple_load', 'text', "Excel 已保存")
        await self._update_progress_smoothly(95, 100, 0.5)
        await ws.send_message('simple_load', 'text', "载荷缩减处理全部完成！")
        
        # 释放内存
        del df_pivot, df_pivot_pd, df_pivot_Romax, df_pivot_Romax_T
        gc.collect()
        
        # 内存监控 - 结束
        log_memory("载荷缩减-全部完成(已释放)", memory_start)
        return count_
