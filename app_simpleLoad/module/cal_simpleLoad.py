from PySide6.QtCore import *
import os
import pandas as pd
import numpy as np
import fnmatch
from typing import List, Dict
from my_websockets.global_ws import ws
from loguru import logger
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
import gc
import pandas as pd
import asyncio


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
        self.result_folder_save_path = result_folder_save_path  # 结果文件夹保存路径
        self.load_file_folder_path = load_file_folder_path  # 时序载荷文件夹
        self.freq_table_path = freq_table_path  # 频次表位置
        self.header = header  # 标题行
        self.conversion_factors = conversion_factors  # 转换系数表单
        #
        self.df_all = None
        self.df_dest = None
        self.df_dic = None
        self.df_ref = None

    def _process_single_file_sync(self, args):
        """同步处理单个文件的函数"""
        file_path, header, conversion_factors, have_time = args
        df = pd.read_csv(
            file_path, 
            sep=r'\s+',
            header=conversion_factors['title_row'],
            names=header,
            dtype=float,
        )
        # 使用numpy向量化操作替代pandas操作
        moment_cols = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]']
        force_cols = ['Fx[KN]', 'Fy[KN]', 'Fz[KN]']
            
        df.loc[:, moment_cols] = df[moment_cols].values / conversion_factors['unit_moment']
        df.loc[:, force_cols] = df[force_cols].values / conversion_factors['unit_force']
        df['speed[rpm]'] *= conversion_factors['unit_speed']

        file_name = os.path.basename(file_path)[:-4]

        names = [file_name]

        index = pd.MultiIndex.from_product([names, df.index], names=['文件名', '序号'])
        df.index = index
        # 计算时间间隔
        if have_time:
            var_time = df['Time[s]'].iloc[-1] - df['Time[s]'].iloc[0]
            var_interval = var_time / (len(df) - 1)
            return file_name, df, [len(df), var_time, var_interval]
        else:
            return file_name, df, [len(df)]

    async def simple_Pre_processing(self):
        """
        说明
        ---
        FUNC 加载文件
        """
        # 释放内存
        self.df_all = None
        self.df_dest = None
        self.df_dic = None
        self.df_ref = None
        gc.collect()

        # 处理Excel
        self.df_ref = pd.read_excel(
            self.freq_table_path,
            names=['文件名', '全寿命发生次数', '仿真时间（s）'],
            header=0,
            index_col=0,
            dtype={'文件名': str}  # 直接指定类型
        )
        have_time = 'Time[s]' in self.header
        if have_time:
            self.df_ref.drop(columns=['仿真时间（s）'], inplace=True)
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
        self.df_dic = {r[0]: r[1] for r in results}
        df_dic1 = {r[0]: r[2] for r in results}

        if have_time:
            df_ref1 = pd.DataFrame.from_dict(
                df_dic1,
                orient='index',
                columns=['载荷行数', '仿真时间（s）', '采样间隔（s）']
            )
        else:
            df_ref1 = pd.DataFrame.from_dict(
                df_dic1,
                orient='index',
                columns=['载荷行数']
            )
            # 使用 loc 添加新行,需要先转置 df_ref1,因为 df_ref1 的索引是文件名
            df_ref1['采样间隔（s）'] = self.df_ref['仿真时间（s）'] / (df_ref1['载荷行数'] - 1)
        df_ref1.rename_axis('文件名', inplace=True)
        self.df_ref = pd.concat([self.df_ref, df_ref1], axis=1)
        self.df_ref['工况占比'] = (
            self.df_ref['仿真时间（s）'] * self.df_ref['全寿命发生次数'] /
            sum(self.df_ref['仿真时间（s）'] * self.df_ref['全寿命发生次数'])
        )
        self.df_all = pd.concat(self.df_dic.values())

    async def simple_load1(self,romax_origin):
        """
        说明
        ---
        FUNC 划分区间
        """
        # 读取从Mx[Nm]~Fz[N]的最大最小值
        logger.info(f"romax_origin: {romax_origin}")
        if self.df_all is not None:
            df_des = self.df_all.describe().T
        else:
            logger.error("df_all is None")
            return
        self.df_dest = pd.concat([df_des['min'], df_des['max']], axis=1)
        # self.df_dest = df_dest.loc[["Fx[KN]", "Fy[KN]", "Fz[KN]", "My[KNm]", "Mz[KNm]"], :]

        # 定义要处理的列
        moment_cols = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]']
        force_cols = ['Fx[KN]', 'Fy[KN]', 'Fz[KN]']

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
        self.max_min = {
            col: create_bins(df_des.loc[col, 'min'], df_des.loc[col, 'max'])
            for col in moment_cols + force_cols
        }
        self.df_dest['min'] = np.floor(self.df_dest['min'])
        self.df_dest['max'] = np.ceil(self.df_dest['max'])
        return self.df_dest.to_json()

    async def savePic(self):
        """使用numpy优化但保持与pandas结果一致"""
        columns = ["Fx[KN]", "Fy[KN]", "Fz[KN]", "Mx[KNm]", "My[KNm]", "Mz[KNm]"]
        weights = {i: self.df_ref.loc[i, '工况占比'] for i in self.df_dic.keys()}
        result_dict = {}
        
        await ws.send_message('simple_load', 'text', "开始生成图表数据...")
        await ws.send_message('simple_load', 'progress', "0")

        for idx, column in enumerate(columns):
            weighted_hist = np.zeros(len(self.max_min[column]) - 1)

            for i, df in self.df_dic.items():
                # 使用numpy的digitize来模拟pandas的cut
                indices = np.digitize(df[column].values, self.max_min[column]) - 1
                # 计算每个区间的计数
                counts = np.bincount(indices, minlength=len(self.max_min[column]) - 1)
                # 归一化
                hist = counts / len(df)
                weighted_hist += hist * weights[i]

            # 创建与pandas相同的区间索引
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

    async def simple_load2(self, table_data, romax_origin):
        # FUNC 载荷缩减
        await ws.send_message('simple_load', 'text', "开始载荷缩减处理...")
        await self._update_progress_smoothly(0, 10, 0.5)
        # 优化数据提取
        lists = [
            [float(value) for value in row.values() if value != '']
            for row in table_data
        ]
        fx, fy, fz, my, mz = lists

        for label_name, col_name, bins in [
            ('fx_label', 'Fx[KN]', fx),
            ('fy_label', 'Fy[KN]', fy),
            ('fz_label', 'Fz[KN]', fz),
            ('my_label', 'My[KNm]', my),
            ('mz_label', 'Mz[KNm]', mz)
        ]:
            # 检查是否单调
            if not all(x <= y for x, y in zip(bins[:-1], bins[1:])):
                return {"message": f"{col_name}的区间值必须是单调递增的", "status": "error"}

            # 使用np.digitize并直接创建标签数组
            indices = np.digitize(self.df_all[col_name].values, bins) - 1
            # 预先创建标签数组
            labels = np.array([f'{label_name[0:2]}{i}' for i in range(1, len(bins))])
            # 直接索引获取标签
            self.df_all[label_name] = labels[indices]

        condition = tuple(item['origin'] for item in romax_origin)

        # 优化merge操作
        df_final = pd.merge(
            self.df_all.reset_index().drop('序号', axis=1),
            self.df_ref,
            on='文件名',
            copy=False
        )

        # 向量化计算格子转速和时间
        df_final['speed[rpm]'] = np.abs(df_final['speed[rpm]'].values)
        interval_life = df_final['采样间隔（s）'].values * df_final['全寿命发生次数'].values
        df_final['格子转速'] = interval_life * df_final['speed[rpm]'].values
        df_final['格子时间'] = interval_life

        # 处理载荷数据
        value_list = ['Mx[KNm]', 'My[KNm]', 'Mz[KNm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        translate_factor = self.conversion_factors['translate_factor']

        # 分块处理数据并确保数值计算的准确性
        data = df_final[value_list].values
        total_elements = data.size
        chunk_size = min(total_elements // 100, 1000000)  # 限制最大块大小
        
        await ws.send_message('simple_load', 'text', "正在进行载荷转换计算...")
        last_progress_reported = 0

        for i in range(0, total_elements, chunk_size):
            chunk = data.flat[i:i + chunk_size]
            # 分别处理正负值，避免无效值
            neg_mask = chunk < 0
            pos_mask = ~neg_mask

            # 处理负值
            if np.any(neg_mask):
                chunk[neg_mask] = -np.power(np.abs(chunk[neg_mask]), translate_factor)

            # 处理正值
            if np.any(pos_mask):
                chunk[pos_mask] = np.power(chunk[pos_mask], translate_factor)

            data.flat[i:i + chunk_size] = chunk

            # 只在进度变化超过10%时更新
            current_progress = round((i + len(chunk)) / total_elements * 100, 1)
            if current_progress - last_progress_reported >= 10:
                # 映射到10-40的进度范围内
                mapped_progress = 10 + (current_progress * 0.3)
                await ws.send_message('simple_load', 'progress', f"{mapped_progress:.1f}")
                last_progress_reported = current_progress

        # 更新DataFrame
        df_final[value_list] = data

        await ws.send_message('simple_load', 'text', "载荷缩减完成")
        await ws.send_message('simple_load', 'text', "正在转换数据")

        # 定义需要处理的列
        speed_cols = ['speed[rpm]', 'My[KNm]', 'Mz[KNm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        processed_cols = ['处理后_speed[rpm]', '处理后_My[KNm]', '处理后_Mz[KNm]', '处理后_Fx[KN]', '处理后_Fy[KN]', '处理后_Fz[KN]']

        # 使用numpy广播代替apply
        grid_speed = df_final['格子转速'].values[:, np.newaxis]
        df_final[processed_cols] = df_final[speed_cols].values * grid_speed
        # 优化pivot_table操作
        index_cols = ['fx_label', 'fy_label', 'fz_label', 'my_label', 'mz_label']
        value_cols = processed_cols + ['格子转速', '格子时间']

        # 执行 pivot_table
        df_pivot = pd.pivot_table(
            df_final,
            index=index_cols,
            values=value_cols,
            aggfunc="sum"
        )
        await ws.send_message('simple_load', 'text', "数据转换完成")
        await self._update_progress_smoothly(10, 40, 1.0)

        # 过滤代码
        df_pivot = df_pivot[(df_pivot != 0).all(axis=1)]
        count_ = df_pivot.shape[0]

        # 计算时间占比并过滤
        df_pivot['时间占比'] = df_pivot['格子转速'] / df_pivot['格子转速'].sum()
        df_pivot = df_pivot[df_pivot['时间占比'] > self.conversion_factors['tol']]

        # 向量化操作替代apply
        grid_speed = df_pivot['格子转速'].values[:, np.newaxis]
        df_ = pd.DataFrame(
            df_pivot[processed_cols].values / grid_speed,
            index=df_pivot.index,
            columns=speed_cols
        )

        await ws.send_message('simple_load', 'text', "正在处理载荷数据...")
        await self._update_progress_smoothly(40, 70, 1.0)

        # 合并数据框并优化map操作
        df_pivot = pd.concat([df_pivot, df_], axis=1)
        # 向量化处理代替map
        cols_to_process = ['My[KNm]', 'Mz[KNm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]']
        data = df_pivot[cols_to_process].values
        inv_factor = 1 / translate_factor

        # 分别处理正负值
        neg_mask = data < 0
        pos_mask = data > 0
        zero_mask = data == 0
        # 创建结果数组
        result = np.zeros_like(data)
        # 处理负值
        if np.any(neg_mask):
            result[neg_mask] = -np.power(np.abs(data[neg_mask]), inv_factor)
        # 处理正值
        if np.any(pos_mask):
            result[pos_mask] = np.power(data[pos_mask], inv_factor)
        # 零值保持为0
        result[zero_mask] = 0

        df_pivot.loc[:, cols_to_process] = result

        # 计算time(h)，使用numpy操作
        total_time = np.sum(self.df_ref['仿真时间（s）'].values * self.df_ref['全寿命发生次数'].values)
        df_pivot['time(h)'] = df_pivot['时间占比'].values * total_time / 3600

        # 重排列并添加工况列
        final_cols = ['time(h)', 'speed[rpm]', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]', 'My[KNm]', 'Mz[KNm]', '时间占比', '格子转速']
        df_pivot = df_pivot[final_cols]

        # 优化工况列创建和索引设置
        loc_numbers = np.arange(1, len(df_pivot) + 1)
        df_pivot = (
            df_pivot
            .reset_index()
            .assign(工况=lambda x: [f'loc{i:03}' for i in loc_numbers])
            .set_index(["fx_label", "fy_label", "fz_label", "my_label", "mz_label"])
        )

        await ws.send_message('simple_load', 'text', "正在保存Excel文件...")
        await self._update_progress_smoothly(70, 95, 1.0)

        excel_name = os.path.basename(self.result_folder_save_path)
        df_pivot.to_excel(f'{self.result_folder_save_path}/Load_Reduction_GL-{excel_name}.xlsx')
        df_pivot_Romax = pd.DataFrame()
        df_pivot_Romax['工况'] = df_pivot['工况']
        df_pivot_Romax['time(h)'] = df_pivot['time(h)']
        df_pivot_Romax['温度(C)'] = [self.conversion_factors['temperature'] for i in range(1, len(df_pivot_Romax) + 1)]
        df_pivot_Romax['speed[rpm]'] = df_pivot['speed[rpm]']

        # TODO 增加载荷关系转换
        df_pivot_Romax['Fx[KN]'] = -1 * df_pivot['Fx[KN]'.replace("x", condition[0]).replace("-", "")] if "-" in condition[0] else df_pivot['Fx[KN]'.replace("x", condition[0])]
        df_pivot_Romax['Fy[KN]'] = -1 * df_pivot['Fy[KN]'.replace("y", condition[1]).replace("-", "")] if "-" in condition[1] else df_pivot['Fy[KN]'.replace("y", condition[1])]
        df_pivot_Romax['Fz[KN]'] = -1 * df_pivot['Fz[KN]'.replace("z", condition[2]).replace("-", "")] if "-" in condition[2] else df_pivot['Fz[KN]'.replace("z", condition[2])]
        df_pivot_Romax['Mx[KNm]'] = -1 * df_pivot['Mx[KNm]'.replace("x", condition[0]).replace("-", "")] if "-" in condition[0] else df_pivot['Mx[KNm]'.replace("x", condition[0])]
        df_pivot_Romax['My[KNm]'] = -1 * df_pivot['My[KNm]'.replace("y", condition[1]).replace("-", "")] if "-" in condition[1] else df_pivot['My[KNm]'.replace("y", condition[1])]

        df_pivot_Romax_T = df_pivot_Romax[['工况', 'Fx[KN]', 'Fy[KN]', 'Fz[KN]', 'Mx[KNm]', 'My[KNm]']].T
        with pd.ExcelWriter(f'{self.result_folder_save_path}/Load_Reduction_Romax-{excel_name}.xlsx') as writer:
            df_pivot_Romax[['工况', 'time(h)', '温度(C)', 'speed[rpm]']].to_excel(writer, sheet_name='工况表格定义', index=False)
            df_pivot_Romax_T.to_excel(writer, sheet_name='载荷', header=False)
            df_pivot_Romax.to_excel(writer, sheet_name='未转置')
            df_pivot_Romax_T.to_excel(writer, sheet_name='已转置')
        await ws.send_message('simple_load', 'text', "Excel 已保存")
        await self._update_progress_smoothly(95, 100, 0.5)
        await ws.send_message('simple_load', 'text', "载荷缩减处理全部完成！")
        return count_
