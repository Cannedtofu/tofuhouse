import requests
import pandas as pd
import time
import datetime
import os

# ========================= 配置区域 (Configuration) =========================
USER_NAME = "MasterTofu996"
USER_AGENT = f'python:popmart_key_words_project:v2.5 (by /u/{USER_NAME})'

KEYWORDS = [
    "popmart", "labubu", "popmart hirono", "popmart skullpanda", 
    "popmart peach riot", "popmart twinkle twinkle", "popmart crybaby", 
    "popmart molly", "popmart dimoo"
]

TEST_MODE = False
POSTS_PER_KEYWORD = 1 if TEST_MODE else 50
SCRAPE_ALL = True

# 性能调节
REQUEST_DELAY = 3.0   # 基础延迟（建议从 2.0 提高到 3.0）
KEYWORD_PAUSE = 60.0  # 每个关键词处理完后，强制休息 60 秒回满“令牌桶”
# ===========================================================================

class PopmartScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.all_results = []

    def fetch_json(self, url, params=None):
        try:
            response = self.session.get(url, params=params, timeout=15)
            
            # 读取 Reddit 官方频率头
            rem = response.headers.get('X-Ratelimit-Remaining')
            reset = response.headers.get('X-Ratelimit-Reset')
            
            if rem is not None:
                rem_val = float(rem)
                # 策略：如果剩余请求不多了，提前减速
                if rem_val < 10:
                    wait_time = float(reset) + 1
                    print(f"🛑 [频率预警] 令牌即将耗尽，MasterTofu996 强制休息 {wait_time}s...")
                    time.sleep(wait_time)
                elif rem_val < 30:
                    # 动态微调：剩余不多时，额外增加 1s 延迟
                    time.sleep(1.0)

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                print("⚠️ 触发 429 限流，进入 90s 长休眠...")
                time.sleep(90)
            return None
        except Exception as e:
            print(f"❌ 网络异常: {e}")
            return None

    def flatten_comments(self, children, post_title, keyword, total_comments, link_id, level=1):
        flat_data = []
        for child in children:
            if child.get('kind') == 't1':
                data = child.get('data', {})
                flat_data.append({
                    "ID": data.get("name"),
                    "父级ID": data.get("parent_id"),
                    "发布时间": datetime.datetime.fromtimestamp(data.get("created_utc", 0)).strftime('%Y-%m-%d %H:%M:%S'),
                    "搜索关键词": keyword,
                    "数据类型": "评论 (Comment)",
                    "层级": f"第 {level} 层",
                    "帖子总评论数": total_comments,
                    "内容正文": data.get("body", ""),
                    "作者": data.get("author", "[deleted]"),
                    "热度(Score)": data.get("score", 0),
                    "所属标题": post_title
                })
                replies = data.get("replies")
                if isinstance(replies, dict):
                    inner = replies.get("data", {}).get("children", [])
                    flat_data.extend(self.flatten_comments(inner, post_title, keyword, total_comments, link_id, level + 1))
        return flat_data

    def save_data(self, keyword_tag):
        if not self.all_results: return
        df = pd.DataFrame(self.all_results).drop_duplicates(subset=['ID'])
        file_name = f"popmart_v2.5_{datetime.date.today()}.xlsx"
        full_path = os.path.join(self.base_path, file_name)
        try:
            df.to_excel(full_path, index=False)
            print(f"💾 [阶段保存] 完成关键词 [{keyword_tag}]，当前总行数: {len(df)}")
        except Exception as e:
            print(f"❌ 保存失败: {e}")

    def run(self):
        run_keywords = [KEYWORDS[0]] if TEST_MODE else KEYWORDS
        print(f"🚀 启动全量采集任务 | 目标帖子总数: {len(run_keywords) * POSTS_PER_KEYWORD}")

        for kw in run_keywords:
            print(f"\n🔍 正在处理关键词: [{kw}]")
            posts_collected = []
            after = None
            
            # 翻页搜索
            while len(posts_collected) < POSTS_PER_KEYWORD:
                search_params = {"q": kw, "sort": "relevance", "t": "all", "limit": 100, "after": after}
                search_data = self.fetch_json("https://www.reddit.com/search.json", search_params)
                if not search_data: break
                children = search_data['data'].get('children', [])
                if not children: break
                posts_collected.extend(children)
                after = search_data['data'].get('after')
                if not after or TEST_MODE: break
                time.sleep(REQUEST_DELAY)

            # 抓取每个帖子
            for i, p in enumerate(posts_collected[:POSTS_PER_KEYWORD]):
                item = p['data']
                title, link_id = item.get('title'), item.get('name')
                total_comments = item.get('num_comments', 0)
                
                print(f"   [{i+1}/{POSTS_PER_KEYWORD}] 抓取中: {title[:20]}...")
                
                self.all_results.append({
                    "ID": link_id, "父级ID": "ROOT", 
                    "发布时间": datetime.datetime.fromtimestamp(item.get("created_utc", 0)).strftime('%Y-%m-%d %H:%M:%S'),
                    "搜索关键词": kw, "数据类型": "帖子 (Post)", "层级": "0", "帖子总评论数": total_comments,
                    "内容正文": item.get("selftext", ""), "作者": item.get("author"), 
                    "热度(Score)": item.get("score"), "所属标题": title
                })

                detail_data = self.fetch_json(f"https://www.reddit.com{item.get('permalink')}.json")
                if detail_data and len(detail_data) > 1:
                    raw_comments = detail_data[1]['data'].get('children', [])
                    self.all_results.extend(self.flatten_comments(raw_comments, title, kw, total_comments, link_id))
                
                # 帖子之间的基础延迟
                time.sleep(REQUEST_DELAY)
            
            # 关键词保存
            self.save_data(kw)
            
            # 关键：关键词之间的大休眠，防止请求堆积
            if kw != run_keywords[-1]:
                print(f"⏳ 关键词处理完毕，强制休眠 {KEYWORD_PAUSE}s 以回满频率配额...")
                time.sleep(KEYWORD_PAUSE)

        print(f"\n🎉 任务已圆满结束！")

if __name__ == "__main__":
    PopmartScraper().run()