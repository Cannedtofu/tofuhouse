import pandas as pd
import datetime
import sys
import os

def analyze_stores(file_path, input_date_str=None):
    """
    分析目标日期与上周同日的杯数趋势。
    返回报告字符串。
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

    # 确定比较日期
    if input_date_str:
        try:
            target_date = datetime.datetime.strptime(input_date_str, "%Y-%m-%d").date()
        except ValueError:
            return f"日期格式无效：'{input_date_str}'。请使用 YYYY-MM-DD。"
    else:
        target_date = datetime.date.today()

    last_week_date = target_date - datetime.timedelta(days=7)

    header = (
        f"--- 周同比 (WoW) 分析配置 ---\n"
        f"本周日期: {target_date}\n"
        f"上周日期: {last_week_date}\n"
        f"------------------------------\n"
    )

    # 拆分为两个数据集
    df_this_week = df[df['Date'] == target_date].copy()
    df_last_week = df[df['Date'] == last_week_date].copy()

    if df_this_week.empty:
        return header + f"警告：未找到本周 ({target_date}) 的数据。"
    if df_last_week.empty:
        return header + "上周数据缺失，分析失败。"

    # 识别同时存在于两份数据中的门店
    stores_this = set(df_this_week['Store Name'].unique())
    stores_last = set(df_last_week['Store Name'].unique())
    common_stores = stores_this.intersection(stores_last)

    if not common_stores:
        return header + "未找到在两个日期中均存在的门店。"

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
        return header + "未找到在两个日期中均存在的门店进行对比。"

    # 城市级别分析
    result = result_store_level.groupby('City').agg(
        Cup_Count_LastWeek=('Cup Count_LastWeek', 'sum'),
        Cup_Count_ThisWeek=('Cup Count_ThisWeek', 'sum'),
        Store_Count=('Store Name', 'count')
    )

    # 添加“总计”行
    total_last = result['Cup_Count_LastWeek'].sum()
    total_this = result['Cup_Count_ThisWeek'].sum()
    total_stores = result['Store_Count'].sum()
    total_wow = ((total_this - total_last) / total_last * 100) if total_last != 0 else 0
    
    result['WoW % Change'] = (
        (result['Cup_Count_ThisWeek'] - result['Cup_Count_LastWeek']) / 
        result['Cup_Count_LastWeek'] * 100
    ).round(2)

    # 准备最终报告
    final_report = result.reset_index()
    
    # 将杯数转换为整数，避免显示为 .0
    final_report['Cup_Count_LastWeek'] = final_report['Cup_Count_LastWeek'].astype(int)
    final_report['Cup_Count_ThisWeek'] = final_report['Cup_Count_ThisWeek'].astype(int)
    
    final_report = final_report[[
        'City', 'Cup_Count_LastWeek', 'Cup_Count_ThisWeek', 'WoW % Change', 'Store_Count'
    ]]
    
    final_report.columns = [
        '城市', '上周杯数', '本周杯数', '周同比涨跌 %', '对比门店数'
    ]

    total_row = pd.DataFrame([{
        '城市': '总计 (TOTAL)',
        '上周杯数': int(total_last),
        '本周杯数': int(total_this),
        '周同比涨跌 %': round(total_wow, 2),
        '对比门店数': int(total_stores)
    }])
    
    final_report = pd.concat([final_report, total_row], ignore_index=True)

    report_text = header + "门店对比分析结果 (按城市分):\n" + final_report.to_string(index=False)
    return report_text

if __name__ == "__main__":
    FILE_PATH = "multi_city_stores.xlsx"
    target_date_input = sys.argv[1] if len(sys.argv) > 1 else None
    print(analyze_stores(FILE_PATH, target_date_input))
