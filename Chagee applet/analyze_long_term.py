import pandas as pd
import datetime
import sys
import os

def analyze_long_term(file_path, output_csv="long_term_analysis.csv"):
    """
    基于 analyze_stores.py 的方法，对数据集中的所有可用日期进行周同比 (WoW) 长效分析。
    """
    if not os.path.exists(file_path):
        return f"错误：未找到文件 '{file_path}'。"

    # 加载数据集
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        return f"读取 Excel 文件时出错：{e}"

    # 基础数据清洗
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    df['Cup Count'] = pd.to_numeric(df['Cup Count'], errors='coerce').fillna(0)
    
    unique_dates = sorted(df['Date'].dropna().unique())
    
    all_results = []
    
    for target_date in unique_dates:
        last_week_date = target_date - datetime.timedelta(days=7)
        
        df_this_week = df[df['Date'] == target_date].copy()
        df_last_week = df[df['Date'] == last_week_date].copy()
        
        if df_last_week.empty:
            continue  # 没有上周数据，无法进行同比分析
            
        # 识别同时存在于两份数据中的门店
        stores_this = set(df_this_week['Store Name'].unique())
        stores_last = set(df_last_week['Store Name'].unique())
        common_stores = stores_this.intersection(stores_last)

        if not common_stores:
            continue
            
        # 过滤并聚合
        df_this_week = df_this_week[df_this_week['Store Name'].isin(common_stores)]
        df_last_week = df_last_week[df_last_week['Store Name'].isin(common_stores)]

        this_week_agg = df_this_week.groupby(['City', 'Store Name'])['Cup Count'].mean().reset_index()
        last_week_agg = df_last_week.groupby(['City', 'Store Name'])['Cup Count'].mean().reset_index()

        result_store_level = pd.merge(
            last_week_agg, 
            this_week_agg, 
            on=['City', 'Store Name'], 
            suffixes=('_LastWeek', '_ThisWeek')
        )

        if result_store_level.empty:
            continue
            
        # 城市级别分析
        result = result_store_level.groupby('City').agg(
            Cup_Count_LastWeek=('Cup Count_LastWeek', 'sum'),
            Cup_Count_ThisWeek=('Cup Count_ThisWeek', 'sum'),
            Store_Count=('Store Name', 'count')
        ).reset_index()
        
        # 添加总计
        total_last = result['Cup_Count_LastWeek'].sum()
        total_this = result['Cup_Count_ThisWeek'].sum()
        total_stores = result['Store_Count'].sum()
        total_wow = ((total_this - total_last) / total_last * 100) if total_last != 0 else 0
        
        result['WoW % Change'] = (
            (result['Cup_Count_ThisWeek'] - result['Cup_Count_LastWeek']) / 
            result['Cup_Count_LastWeek'] * 100
        ).round(2)
        
        result['Date'] = target_date
        
        # 将杯数转换为整数
        result['Cup_Count_LastWeek'] = result['Cup_Count_LastWeek'].astype(int)
        result['Cup_Count_ThisWeek'] = result['Cup_Count_ThisWeek'].astype(int)
        
        total_row = {
            'Date': target_date,
            'City': '总计 (TOTAL)',
            'Cup_Count_LastWeek': int(total_last),
            'Cup_Count_ThisWeek': int(total_this),
            'WoW % Change': round(total_wow, 2),
            'Store_Count': int(total_stores)
        }
        
        total_df = pd.DataFrame([total_row])
        result = pd.concat([result, total_df], ignore_index=True)
        
        all_results.append(result)
        
    if not all_results:
        return "没有满足周同比条件的日期数据可以生成长效分析。"
        
    final_report = pd.concat(all_results, ignore_index=True)
    
    # 调整列顺序
    final_report = final_report[[
        'Date', 'City', 'Cup_Count_LastWeek', 'Cup_Count_ThisWeek', 'WoW % Change', 'Store_Count'
    ]]
    
    final_report.columns = [
        '日期', '城市', '上周杯数', '本周杯数', '周同比涨跌 %', '对比门店数'
    ]
    
    try:
        final_report.to_csv(output_csv, index=False, encoding='utf-8-sig')
        min_date = final_report['日期'].min()
        max_date = final_report['日期'].max()
        return f"长效分析报告已成功生成，包含从 {min_date} 到 {max_date} 的数据。\n报告已保存至：{output_csv}"
    except Exception as e:
        return f"保存报告时出错：{e}"

if __name__ == "__main__":
    # 可以通过命令行参数指定输入和输出文件
    FILE_PATH = "multi_city_stores.xlsx"
    OUTPUT_FILE = "long_term_analysis.csv"
    
    if len(sys.argv) > 1:
        FILE_PATH = sys.argv[1]
    if len(sys.argv) > 2:
        OUTPUT_FILE = sys.argv[2]
        
    print(analyze_long_term(FILE_PATH, OUTPUT_FILE))
