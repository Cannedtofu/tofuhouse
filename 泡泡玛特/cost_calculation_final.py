import json
import os
import glob
from google import genai

# ==================== 核心配置 ====================
API_KEY = "AIzaSyCPCdPj75PoBjfhX5xzGHcw2A1Ei_N-qkU"
RAW_DATA_DIR = "raw_batch_results"  # 你的原始数据存放目录
JOB_LOG_FILE = "batch_job_log.txt"  # 任务日志

# Gemini 2.5 Flash (Batch) 估算费率 ($/1M Tokens)
PRICE_IN_1M = 0.15 
PRICE_OUT_1M = 0.60 

client = genai.Client(api_key=API_KEY)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# =================================================

def ensure_raw_files_exist():
    """确保所有完成的任务都下载了原始文件"""
    log_path = os.path.join(BASE_DIR, JOB_LOG_FILE)
    if not os.path.exists(log_path): return []
    
    downloaded_count = 0
    save_dir = os.path.join(BASE_DIR, RAW_DATA_DIR)
    if not os.path.exists(save_dir): os.makedirs(save_dir)

    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            if ":" in line:
                _, job_id = line.strip().split(":", 1)
                job_id = job_id.strip()
                
                # 检查本地是否已有文件
                safe_name = job_id.replace("/", "_").replace(":", "")
                local_path = os.path.join(save_dir, f"raw_{safe_name}.jsonl")
                
                if not os.path.exists(local_path):
                    print(f"📥 补下载结果文件: {job_id} ...")
                    try:
                        job = client.batches.get(name=job_id)
                        if job.state == "JOB_STATE_SUCCEEDED" or str(job.state).endswith("SUCCEEDED"):
                            fname = job.dest.file_name if hasattr(job.dest, 'file_name') else str(job.dest)
                            content = client.files.download(file=fname)
                            with open(local_path, 'wb') as f_out:
                                f_out.write(content)
                            downloaded_count += 1
                    except Exception as e:
                        print(f"   ❌ 下载失败: {e}")
    
    if downloaded_count > 0:
        print(f"✅ 已补全 {downloaded_count} 个缺失的文件。")

def audit_local_files():
    # 1. 确保文件齐全
    ensure_raw_files_exist()
    
    raw_dir = os.path.join(BASE_DIR, RAW_DATA_DIR)
    jsonl_files = glob.glob(os.path.join(raw_dir, "*.jsonl"))
    
    if not jsonl_files:
        print(f"❌ 在 {RAW_DATA_DIR} 目录下没有找到数据文件。")
        return

    print(f"\n📊 正在审计 {len(jsonl_files)} 个结果文件...")
    
    total_input = 0
    total_output = 0
    valid_lines = 0
    
    for file_path in jsonl_files:
        filename = os.path.basename(file_path)
        file_in = 0
        file_out = 0
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    # 从 usageMetadata 提取 Token (注意 API 返回通常是驼峰命名)
                    # 路径通常是 data['response']['usageMetadata']
                    # 或者如果是 inline response，结构可能略有不同
                    
                    usage = {}
                    if 'response' in data and 'usageMetadata' in data['response']:
                        usage = data['response']['usageMetadata']
                    elif 'usageMetadata' in data:
                         usage = data['usageMetadata']
                    
                    if usage:
                        # 兼容 promptTokenCount 和 prompt_token_count
                        i_tokens = usage.get('promptTokenCount', usage.get('prompt_token_count', 0))
                        o_tokens = usage.get('candidatesTokenCount', usage.get('candidates_token_count', 0))
                        
                        file_in += i_tokens
                        file_out += o_tokens
                        valid_lines += 1
                except:
                    pass
        
        total_input += file_in
        total_output += file_out
        # print(f"   📄 {filename}: In={file_in}, Out={file_out}")

    # 计算最终费用
    cost_in = (total_input / 1_000_000) * PRICE_IN_1M
    cost_out = (total_output / 1_000_000) * PRICE_OUT_1M
    total_cost = cost_in + cost_out

    print("\n" + "="*50)
    print(f"✅ 审计完成 (共处理 {valid_lines} 条数据)")
    print("-" * 50)
    print(f"Total Input Tokens : {total_input:>12,}  ( x ${PRICE_IN_1M}/1M )")
    print(f"Total Output Tokens: {total_output:>12,}  ( x ${PRICE_OUT_1M}/1M )")
    print("-" * 50)
    print(f"💰 最终预估成本    : ${total_cost:>12.4f} USD")
    print("="*50)

if __name__ == "__main__":
    audit_local_files()