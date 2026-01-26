import os
import re
import pandas as pd
import paddle
from paddlenlp import Taskflow
from tqdm import tqdm

# 1. 环境加固：禁用静态图防止加载异常，设置短路径解决 Windows 路径限制
paddle.disable_static()
os.environ['PPNLP_HOME'] = r'D:\ppnlp_models'

class FinalShowProcessor:
    def __init__(self, batch_size=32):
        self.batch_size = batch_size
        print("正在初始化 Taskflow 最终优化版...")
        # use_fast=False 确保获取 segs/tags 结构
        self.lac = Taskflow("lexical_analysis", use_fast=False)
        
        # 深度黑名单：人工定义的“行业噪音”，防止模型误判为艺人
        self.black_list = {
            '巡演', '演唱会', '专场', '音乐会', '特别', '项目', '年会', '派对', 
            '狂欢', '之内', '之外', '时间', '限定', '冬日', '系列', '学生', 
            '音乐节', '歌手', '别名', '年代', '飞行日', '站', '现场', '北京', '上海'
        }

    def aggressive_clean(self, title):
        """
        核心优化：送入 NLP 前的深度清洗（脱水）
        """
        if not isinstance(title, str) or title.strip() == "":
            return "NULL_TITLE"
        
        # 1. 移除开头城市标签
        text = re.sub(r'^[【\[\(（].*?[】\]\)）]\s*', '', title)
        
        # 2. 关键：物理删除引号、书名号及括号内的内容（解决“羽果/年会”等干扰）
        text = re.sub(r'[「《“\[\(].*?[」》”\]\)）]', ' ', text)
        
        # 3. 关键词截断：在“演唱会”等关键词处物理切断，只取前面的主体
        text = re.split(r'演唱会|巡演|专场|音乐会|Tour|——| 202', text, flags=re.IGNORECASE)[0]
        
        # 4. 剔除年份数字
        text = re.sub(r'202\d|201\d', '', text)
        
        res = text.strip()
        return res if res else "NULL_TITLE"

    def extract_with_tiered_priority(self, titles):
        """
        逻辑优化：标签优先级 + 严格索引对齐
        """
        results = []
        for i in tqdm(range(0, len(titles), self.batch_size), desc="提取中"):
            batch = titles[i : i + self.batch_size]
            try:
                lac_res = self.lac(batch)
                for idx in range(len(batch)):
                    try:
                        item = lac_res[idx]
                        segs = item.get('segs', [])
                        tags = item.get('tags', [])
                        
                        # 第一优先级：PER(人名), ORG(团体)
                        tier1 = [s for s, t in zip(segs, tags) if t in ('PER', 'ORG') and s not in self.black_list]
                        # 第二优先级：nz(专名), nw(新词), n(名)
                        tier2 = [s for s, t in zip(segs, tags) if t in ('nz', 'nw', 'n') and s not in self.black_list]
                        
                        # 过滤纯英文大写后缀（解决 ICONIC, OGS 等干扰）
                        tier2 = [s for s in tier2 if not re.match(r'^[A-Z0-9\s/]+$', s)]

                        # 决定最终提取列表
                        extracted = tier1 if tier1 else tier2
                        
                        # 物理合并：不再用斜杠，将分词拼接（如 声音+玩具 -> 声音玩具）
                        # 仅取前两个分段进行合并，通常足以包含完整的名称
                        final_artist = "".join(extracted[:2]).strip()
                        results.append(final_artist)
                    except:
                        results.append("")
            except Exception as e:
                print(f"批次异常: {e}")
                results.extend([""] * len(batch))
        return results

    def run(self, input_path, output_path):
        print(f"读取文件: {input_path}")
        df = pd.read_csv(input_path, encoding='utf_8_sig') if input_path.endswith('.csv') else pd.read_excel(input_path)
        
        # 1. 预清洗：生成 NLP 输入列
        df['NLP_Input'] = df['Title'].apply(self.aggressive_clean)
        
        # 2. 独立标题去重识别
        unique_titles = df['NLP_Input'].unique().tolist()
        print(f"需识别的唯一标题数: {len(unique_titles)}")
        
        artist_results = self.extract_with_tiered_priority(unique_titles)
        
        # 3. 映射逻辑
        mapping = dict(zip(unique_titles, artist_results))
        df['Group_Artist'] = df['NLP_Input'].map(mapping)
        
        # 4. 最终保存：保留 NLP_Input 以便观察
        df['Group_Artist'] = df['Group_Artist'].replace("NULL_TITLE", "")
        df.to_csv(output_path, index=False, encoding='utf_8_sig')
        print(f"🎉 处理完成！结果已保存至: {output_path}")

if __name__ == "__main__":
    processor = FinalShowProcessor(batch_size=32)
    processor.run(r"D:\代码项目\concerts_data_combined.csv", r"D:\代码项目\concerts_data_combined_marked.csv")