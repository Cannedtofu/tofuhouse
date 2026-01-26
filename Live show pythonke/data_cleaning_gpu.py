import os
import re
import pandas as pd
import paddle
from paddlenlp import Taskflow
from tqdm import tqdm

# 强制环境配置：GPU 运行
paddle.set_device('gpu') 
os.environ['PPNLP_HOME'] = r'D:\ppnlp_models'

class TripleTierShowProcessor:
    def __init__(self, uie_batch=16, lac_batch=64):
        self.uie_batch = uie_batch
        self.lac_batch = lac_batch
        
        # 1. 定义分层 Schema
        self.high_schema = ['歌手', '乐队']
        self.med_schema = ['人物', '人', '团体']
        self.full_schema = self.high_schema + self.med_schema
        
        print(f"正在初始化 UIE 模型 (Schema: {self.full_schema})...")
        self.uie = Taskflow("information_extraction", 
                            schema=self.full_schema, 
                            model="uie-base", 
                            device_id=0)
        
        print("正在初始化 LAC 补漏模型...")
        self.lac = Taskflow("lexical_analysis", use_fast=False)
        
        # 深度黑名单：剔除行业噪音词
        self.black_list = {'巡演', '演唱会', '专场', '音乐会', '站', '项目', '特别', '系列', '年代', '北京', '上海'}

    def clean_only_city_tag(self, title):
        """输入处理：仅去除开头【城市】标签"""
        if not isinstance(title, str) or title.strip() == "":
            return "NULL_TITLE"
        text = re.sub(r'^[【\[\(（].*?[】\]\)）]\s*', '', title)
        return text.strip() if text.strip() else "NULL_TITLE"

    def process_triple_tier(self, titles):
        """
        三级级联识别核心逻辑：High(UIE精细) -> Medium(UIE模糊) -> Low(LAC物理)
        """
        final_artists = [""] * len(titles)
        confidence_tags = ["Unidentified"] * len(titles)
        
        # --- [第一步] 执行 UIE 全量识别 ---
        uie_results = []
        for i in tqdm(range(0, len(titles), self.uie_batch), desc="[1/2] UIE 深度识别"):
            batch = titles[i : i + self.uie_batch]
            try:
                uie_results.extend(self.uie(batch))
            except:
                uie_results.extend([{}] * len(batch))

        # --- [第二步] 分层逻辑过滤 ---
        lac_queue = [] # 存储两级 UIE 均失效的索引
        
        for idx, res in enumerate(uie_results):
            # 1. 尝试高精度匹配 (歌手/乐队)
            high_found = []
            for label in self.high_schema:
                if label in res:
                    high_found.extend([item['text'] for item in res[label]])
            
            if high_found:
                final_artists[idx] = "/".join(list(dict.fromkeys(high_found)))
                confidence_tags[idx] = "High (UIE-Specific)"
                continue
            
            # 2. 尝试中精度匹配 (人物/人/团体)
            med_found = []
            for label in self.med_schema:
                if label in res:
                    med_found.extend([item['text'] for item in res[label]])
            
            if med_found:
                # 过滤掉一些单字结果以保证中精度质量
                med_found = [m for m in med_found if len(m) > 1 and m not in self.black_list]
                if med_found:
                    final_artists[idx] = "/".join(list(dict.fromkeys(med_found)))
                    confidence_tags[idx] = "Medium (UIE-General)"
                    continue
            
            # 3. 若 UIE 均未识别，加入 LAC 补漏队列
            lac_queue.append(idx)

        # --- [第三步] LAC 补漏 (Low Precision) ---
        if lac_queue:
            print(f">> 共有 {len(lac_queue)} 条数据进入 LAC 低精度补漏...")
            queue_titles = [titles[i] for i in lac_queue]
            lac_results = []
            for i in tqdm(range(0, len(queue_titles), self.lac_batch), desc="[2/2] LAC 补漏中"):
                batch = queue_titles[i : i + self.lac_batch]
                try:
                    res_batch = self.lac(batch)
                    for item in res_batch:
                        segs, tags = item.get('segs', []), item.get('tags', [])
                        # 识别 PER, ORG, nz, nw
                        found = [s for s, t in zip(segs, tags) 
                                if t in ('PER', 'ORG', 'nz', 'nw') and s not in self.black_list and len(s) > 1]
                        
                        # 物理合并前两个词
                        artist = "".join(found[:2]) if found else ""
                        lac_results.append(artist)
                except:
                    lac_results.extend([""] * len(batch))
            
            # 回填 LAC 结果
            for i, original_idx in enumerate(lac_queue):
                if lac_results[i]:
                    final_artists[original_idx] = lac_results[i]
                    confidence_tags[original_idx] = "Low (LAC Fallback)"

        return final_artists, confidence_tags

    def run(self, input_path, output_path):
        df = pd.read_csv(input_path, encoding='utf_8_sig') if input_path.endswith('.csv') else pd.read_excel(input_path)
        
        # 1. 预处理保留全称
        df['NLP_Input'] = df['Title'].apply(self.clean_only_city_tag)
        
        # 2. 去重映射以节省计算资源
        unique_titles = df['NLP_Input'].unique().tolist()
        print(f"独立标题识别数: {len(unique_titles)}")
        
        artists, confidences = self.process_triple_tier(unique_titles)
        
        # 3. 构建映射表
        map_art = dict(zip(unique_titles, artists))
        map_conf = dict(zip(unique_titles, confidences))
        
        # 4. 映射回原表
        df['Group_Artist'] = df['NLP_Input'].map(map_art)
        df['Confidence_Level'] = df['NLP_Input'].map(map_conf)
        
        # 保存，保留 NLP_Input 方便审查
        df.to_csv(output_path, index=False, encoding='utf_8_sig')
        print(f"🎉 处理完成！结果文件：{output_path}")

if __name__ == "__main__":
    processor = TripleTierShowProcessor(uie_batch=16, lac_batch=64)
    processor.run(r"D:\代码项目\concerts_data_combined.csv", r"D:\代码项目\concerts_data_combined_marked_gpu.csv")