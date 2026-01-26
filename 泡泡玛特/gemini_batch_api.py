import os
import time
from google import genai
from google.genai import types

# ==================== 可调整参数区域 ====================
CONFIG = {
    "api_key": "YOUR_GEMINI_API_KEY",  # 填入你的 Gemini API Key
    "file_path": "popmart_batch_input.jsonl", # 之前生成的 jsonl 文件路径
    "model_name": "models/gemini-1.5-flash",  # 任务使用的模型
    "display_name": "Popmart_Sentiment_Analysis_Batch", # 任务显示名称
}
# ======================================================

def run_batch_job():
    # 1. 初始化客户端
    client = genai.Client(api_key=CONFIG["api_key"])
    
    print(f"正在上传文件: {CONFIG['file_path']} ...")
    
    # 2. 上传 JSONL 文件到 Google File API
    # 注意：Batch 任务要求文件必须先上传
    uploaded_file = client.files.upload(
        file=CONFIG["file_path"],
        config=types.UploadFileConfig(
            display_name=CONFIG["display_name"],
            mime_type="application/jsonl"  # 明确指定为 jsonl 格式
        )
    )
    print(f"文件上传成功，File ID: {uploaded_file.name}")

    # 3. 创建 Batch 任务
    print(f"正在创建 Batch 任务 (使用模型: {CONFIG['model_name']}) ...")
    batch_job = client.batches.create(
        model=CONFIG["model_name"],
        src=uploaded_file.name,  # 使用刚上传的文件名作为来源
        config={
            "display_name": CONFIG["display_name"]
        }
    )
    
    job_name = batch_job.name
    print(f"Batch 任务已成功提交！")
    print(f"任务 ID: {job_name}")
    print("-" * 30)

    # 4. 循环监控任务状态
    # Batch 任务通常在 24 小时内完成，但 2 万条数据通常几小时即可
    while True:
        status_job = client.batches.get(name=job_name)
        state = status_job.state.name # 获取当前状态
        
        print(f"[{time.strftime('%H:%M:%S')}] 当前任务状态: {state}")
        
        if state == "SUCCEEDED":
            print("\n恭喜！任务已完成。")
            # 获取输出文件的名称
            output_file = status_job.output_info.file_path if hasattr(status_job, 'output_info') else "查看 AI Studio 下载"
            print(f"结果已生成。你可以前往 Google AI Studio 下载结果，或使用 client.files.download 获取。")
            break
        elif state in ["FAILED", "CANCELLED"]:
            print(f"\n任务异常终止: {state}")
            if hasattr(status_job, 'error'):
                print(f"错误信息: {status_job.error}")
            break
        
        # 每隔 5 分钟检查一次状态（Batch 任务无需频繁轮询）
        time.sleep(300) 

if __name__ == "__main__":
    run_batch_job()