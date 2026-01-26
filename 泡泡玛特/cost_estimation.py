import pandas as pd
import math
import os

# ================= 配置区域 =================
# 动态获取当前脚本所在文件夹的路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_NAME = "popmart_v2.0_FULL.xlsx"
INPUT_FILE = os.path.join(BASE_DIR, FILE_NAME)

# Gemini 1.5 Flash Batch API 2026 预估价格 (每 100 万 tokens)
# Batch API 通常比实时调用便宜 50%
PRICE_PER_1M_INPUT_USD = 0.0375 
PRICE_PER_1M_OUTPUT_USD = 0.15
# ===========================================

def estimate_costs():
    if not os.path.exists(INPUT_FILE):
        print(f"错误：在路径 {INPUT_FILE} 下未找到文件。请检查文件名是否准确。")
        return

    try:
        df = pd.read_excel(INPUT_FILE)
    except Exception as e:
        print(f"读取文件失败: {e}。请确保已安装 openpyxl (pip install openpyxl)")
        return

    # 构建 ID 到正文的映射
    text_map = dict(zip(df['ID'].astype(str), df['内容正文'].astype(str)))
    
    total_input_chars = 0
    total_rows = len(df)
    
    for _, row in df.iterrows():
        root_title = str(row.get('所属标题', ''))
        parent_id = str(row.get('父级ID', ''))
        target_text = str(row.get('内容正文', ''))
        
        parent_text = text_map.get(parent_id, "N/A")
        
        # 预估 Prompt 模版长度 (指令约 1500 字符)
        context_len = len(root_title) + len(parent_text) + len(target_text) + 1500
        total_input_chars += context_len

    est_input_tokens = total_input_chars / 4
    est_output_tokens = total_rows * 800  # 17维度的JSON平均长度
    
    cost_input = (est_input_tokens / 1_000_000) * PRICE_PER_1M_INPUT_USD
    cost_output = (est_output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT_USD
    
    print(f"--- 路径检查成功 ---")
    print(f"当前路径: {BASE_DIR}")
    print(f"文件位置: {INPUT_FILE}")
    print(f"--- 预估报告 ---")
    print(f"总处理行数: {total_rows}")
    print(f"预计输入 Tokens: {math.ceil(est_input_tokens):,}")
    print(f"预计输出 Tokens: {math.ceil(est_output_tokens):,}")
    print(f"预计总费用 (Batch API): ${cost_input + cost_output:.4f} USD")

if __name__ == "__main__":
    estimate_costs()