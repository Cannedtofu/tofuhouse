import os
import re
import pandas as pd
import paddle
from paddlenlp import Taskflow
from openai import OpenAI
from tqdm import tqdm

# 环境配置
paddle.set_device('gpu') 
os.environ['PPNLP_HOME'] = r'D:\ppnlp_models'

class UltimateHybridProcessor:
    def __init__(self, batch_size_uie=16):
        self.batch_size_uie = batch_size_uie
        
        # 1. 初始化 Qwen (Ollama)
        self.client = OpenAI(base_url='http://localhost:11434/v1', api_key='ollama')
        
        # 2. 初始化 UIE (两级 Schema)
        self.full_schema = ['歌手', '乐队', '人物', '人', '团体']
        print("正在加载 UIE & LAC 模型...")
        self.uie = Taskflow("information_extraction", schema=self.full_schema, device_id=0)
        
        # 3. 初始化 LAC
        self.lac = Taskflow("lexical_analysis", use_fast=False)
        
        self.black_list = {'巡演', '演唱会', '专场', '音乐会', '站', '项目', '特别', '系列', '年代'}

    def clean_only_city_tag(self, title):
        """预处理：仅去除开头【城市】"""
        if not isinstance(title, str) or title.strip() == "": return "NULL_TITLE"
        return re.sub(r'^[【\[\(（].*?[】\]\)）]\s*', '', title).strip()

    def call_qwen(self, title):
        """第一级识别：Qwen"""
        prompt = f"从演出标题中提取歌手或乐队名，只输出名字，多个用/分隔，无结果输出'未识别'。标题：{title}\n结果："
        try:
            response = self.client.chat.completions.create(
                model="qwen2.5:7b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=50,
                timeout=5 # 设置超时防止阻塞
            )
            res = response.choices[0].message.content.strip()
            return res if "未识别" not in res and len(res) > 1 else None
        except:
            return None

    def process_data(self, titles):
        """
        核心级联逻辑：Qwen -> UIE Specific -> UIE General -> LAC
        """
        final_artists = [""] * len(titles)
        confidence_levels = ["Unidentified"] * len(titles)
        
        # --- 步骤 1: Qwen 优先识别 (针对去重后的 titles) ---
        print(f">> 启动第一级 QW 识别 (共 {len(titles)} 条)...")
        for i in tqdm(range(len(titles)), desc="Qwen 推理"):
            qwen_res = self.call_qwen(titles[i])
            if qwen_res:
                final_artists[i] = qwen_res
                confidence_levels[i] = "QW"

        # --- 步骤 2: UIE 补漏 (针对 Qwen 失败的项) ---
        remaining_indices = [idx for idx, val in enumerate(final_artists) if val == ""]
        if remaining_indices:
            print(f">> 启动 UIE 补漏 (共 {len(remaining_indices)} 条)...")
            rem_titles = [titles[i] for i in remaining_indices]
            
            uie_raw = []
            for i in tqdm(range(0, len(rem_titles), self.batch_size_uie), desc="UIE 识别"):
                batch = rem_titles[i : i + self.batch_size_uie]
                try:
                    uie_raw.extend(self.uie(batch))
                except:
                    uie_raw.extend([{}] * len(batch))
            
            # 分层解析 UIE 结果
            for idx, res in enumerate(uie_raw):
                original_idx = remaining_indices[idx]
                
                # UIE Specific (High)
                high_found = []
                for label in ['歌手', '乐队']:
                    if label in res: high_found.extend([item['text'] for item in res[label]])
                if high_found:
                    final_artists[original_idx] = "/".join(list(dict.fromkeys(high_found)))
                    confidence_levels[original_idx] = "High (UIE-Specific)"
                    continue
                
                # UIE General (Medium)
                med_found = []
                for label in ['人物', '人', '团体']:
                    if label in res: med_found.extend([item['text'] for item in res[label]])
                med_found = [m for m in med_found if len(m) > 1 and m not in self.black_list]
                if med_found:
                    final_artists[original_idx] = "/".join(list(dict.fromkeys(med_found)))
                    confidence_levels[original_idx] = "Medium (UIE-General)"

        # --- 步骤 3: LAC 最终兜底 (针对前两级均失败的项) ---
        lac_indices = [idx for idx, val in enumerate(final_artists) if val == ""]
        if lac_indices:
            print(f">> 启动 LAC 最终兜底 (共 {len(lac_indices)} 条)...")
            lac_titles = [titles[i] for i in lac_indices]
            
            try:
                lac_raw = self.lac(lac_titles)
                for idx, item in enumerate(lac_raw):
                    orig_idx = lac_indices[idx]
                    segs, tags = item.get('segs', []), item.get('tags', [])
                    found = [s for s, t in zip(segs, tags) if t in ('PER', 'ORG', 'nz', 'nw') and s not in self.black_list and len(s) > 1]
                    if found:
                        final_artists[orig_idx] = "".join(found[:2])
                        confidence_levels[orig_idx] = "Low (LAC Fallback)"
            except:
                pass

        return final_artists, confidence_levels

    def run(self, input_path, output_path):
        df = pd.read_csv(input_path, encoding='utf_8_sig')
        df['NLP_Input'] = df['Title'].apply(self.clean_only_city_tag)
        
        # 去重优化
        unique_titles = df['NLP_Input'].unique().tolist()
        artists, confs = self.process_data(unique_titles)
        
        # 映射
        map_art = dict(zip(unique_titles, artists))
        map_conf = dict(zip(unique_titles, confs))
        
        df['Group_Artist'] = df['NLP_Input'].map(map_art)
        df['Confidence_Level'] = df['NLP_Input'].map(map_conf)
        
        df.to_csv(output_path, index=False, encoding='utf_8_sig')
        print(f"🎉 任务完成！结果存至: {output_path}")

if __name__ == "__main__":
    processor = UltimateHybridProcessor(batch_size_uie=16)
    processor.run(r"D:\代码项目\concerts_data_combined.csv", r"D:\代码项目\concerts_data_combined_qwen.csv")