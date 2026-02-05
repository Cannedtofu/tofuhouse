import pandas as pd
import json
import os
import time
from google import genai

# ==================== 核心配置 ====================
API_KEY = "AIzaSyCPCdPj75PoBjfhX5xzGHcw2A1Ei_N-qkU"
ORIGINAL_EXCEL = "popmart_analyze_test.xlsx"
OUTPUT_EXCEL = "popmart_analysis_FINAL_PARTIAL.xlsx"
JOB_LOG_FILE = "batch_job_log.txt" 
RAW_DATA_DIR = "raw_batch_results"  # [新增] 用于存放原始 JSONL 文件的目录
# =================================================

client = genai.Client(api_key=API_KEY)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_job_ids_from_log():
    """从日志文件中解析 Job ID"""
    log_path = os.path.join(BASE_DIR, JOB_LOG_FILE)
    ids = []
    
    if not os.path.exists(log_path):
        print(f"⚠️ 警告：未找到日志文件 {log_path}")
        return ids

    print(f"Reading job IDs from {JOB_LOG_FILE}...")
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if ":" in line:
                try:
                    _, job_id = line.split(":", 1)
                    job_id = job_id.strip()
                    if job_id.startswith("batches/"):
                        ids.append(job_id)
                except ValueError:
                    continue
    
    ids = list(set(ids))
    print(f"Loaded {len(ids)} unique Job IDs.")
    return ids

def merge_results():
    # 1. 动态加载 Job ID
    job_ids = load_job_ids_from_log()
    
    if not job_ids:
        print("❌ No Job IDs found in log file.")
        return

    # 2. 读取原始 Excel
    input_path = os.path.join(BASE_DIR, ORIGINAL_EXCEL)
    if not os.path.exists(input_path):
        print(f"❌ Original file not found: {input_path}")
        return

    print(f"Reading original Excel data...")
    df_final = pd.read_excel(input_path)
    df_final['ID'] = df_final['ID'].astype(str)
    
    # [新增] 确保原始数据保存目录存在
    raw_save_path = os.path.join(BASE_DIR, RAW_DATA_DIR)
    if not os.path.exists(raw_save_path):
        os.makedirs(raw_save_path)
        print(f"Created directory for raw results: {raw_save_path}")
    
    all_extracted_data = []

    # 3. 遍历所有加载的 ID
    for job_id in job_ids:
        try:
            print(f"\nChecking Job: {job_id}")
            job = client.batches.get(name=job_id)
            
            # 判断状态
            current_state = str(job.state).upper()
            if job.done or "SUCCEEDED" in current_state:
                # 提取 file_name
                raw_file_resource_name = job.dest.file_name if hasattr(job.dest, 'file_name') else str(job.dest)
                
                if not raw_file_resource_name or "None" in raw_file_resource_name:
                    print(f"   ⚠️ Job completed but no valid output file found.")
                    continue
                
                print(f"   ✅ Job Completed. Downloading: {raw_file_resource_name}")
                
                # 下载结果 (Bytes)
                content = client.files.download(file=raw_file_resource_name)
                
                # ==================== [新增功能] 保存原始数据 ====================
                # 将 job_id 中的特殊字符处理掉作为文件名 (例如 batches/123 -> batch_123)
                safe_name = job_id.replace("/", "_").replace(":", "")
                local_raw_filename = f"raw_{safe_name}.jsonl"
                local_raw_file_path = os.path.join(raw_save_path, local_raw_filename)
                
                # 以二进制写入模式保存 ('wb') 因为 content 是 bytes
                with open(local_raw_file_path, 'wb') as f_raw:
                    f_raw.write(content)
                print(f"   💾 Raw data saved to: {local_raw_filename}")
                # ===============================================================
                
                # 解析内容
                lines = content.decode('utf-8').splitlines()
                valid_count = 0
                for line in lines:
                    if not line.strip(): continue
                    data = json.loads(line)
                    
                    response = data.get('response', {})
                    if 'error' in response: 
                        continue
                    
                    candidates = response.get('candidates', [])
                    if not candidates: continue

                    raw_id = data['custom_id'].replace("row_", "")
                    json_text = candidates[0]['content']['parts'][0]['text']
                    
                    # 清理 Markdown
                    json_text = json_text.replace("```json", "").replace("```", "").strip()
                    
                    try:
                        analysis_result = json.loads(json_text)
                        analysis_result['ID'] = raw_id
                        all_extracted_data.append(analysis_result)
                        valid_count += 1
                    except json.JSONDecodeError:
                        print(f"   ❌ JSON Parse Error for ID {raw_id}")
                
                print(f"   --> Extracted {valid_count} valid records.")

            else:
                print(f"   ⏳ Job Status: {job.state} (Not Completed)")
                    
        except Exception as e:
            print(f"❌ Error processing {job_id}: {e}")

    # 4. 合并并保存
    if all_extracted_data:
        print(f"\nMerging {len(all_extracted_data)} results...")
        df_results = pd.DataFrame(all_extracted_data)
        
        # 去重
        df_results = df_results.drop_duplicates(subset=['ID'], keep='last')
        
        # 合并
        df_merged = pd.merge(df_final, df_results, on='ID', how='left')
        
        output_path = os.path.join(BASE_DIR, OUTPUT_EXCEL)
        df_merged.to_excel(output_path, index=False)
        print(f"🎉 Success! Final file saved to: {output_path}")
    else:
        print("\n😭 No data extracted in this run.")

if __name__ == "__main__":
    merge_results()