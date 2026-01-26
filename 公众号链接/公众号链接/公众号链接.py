# -*- coding: utf-8 -*-

import traceback
import requests
import pandas as pd
 
 
 
class WeChatSpider:
    def __init__(self, cookie, token):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Cookie": cookie,
        }
        self.base_params = {
            "lang": "zh_CN",
            "f": "json",
            "token": token,
        }
 
    def get_fakeid(self, nickname, begin=0, count=5):
        #获取公众号的 FakeID
        search_url = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
        params = {
            **self.base_params,
            "action": "search_biz",
            "query": nickname,
            "begin": begin,
            "count": count,
            "ajax": "1",
        }
 
        try:
            response = self.session.get(search_url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            if "list" in data and data["list"]:
                return data["list"][0].get("fakeid")
            return None
        except Exception as e:
            raise Exception(f"failed to get {nickname} fakeid: {traceback.format_exc()}")
 
    def get_articles(self, fakeid, begin=0, count=29):
        #获取公众号的文章列表并翻页
        all_articles = []
        while True:
            art_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
            params = {
                **self.base_params,
                "query": "",
                "begin": begin,
                "count": count,
                "type": 9,
                "action": "list_ex",
                "fakeid": fakeid,
            }
 
            try:
                response = self.session.get(art_url, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                if "app_msg_list" in data:
                    articles = [
                        {
                            "title": item.get("title"),
                            "link": item.get("link")
                        }
                        for item in data["app_msg_list"]
                    ]
                    all_articles.extend(articles)
 
                    # 判断是否有下一页
                    if len(data["app_msg_list"]) < count:
                        break  # 如果当前页的文章数少于请求的数量，表示已获取所有文章
                    else:
                        begin += count  # 否则，翻到下一页，继续获取
                else:
                    break
            except Exception as e:
                raise Exception(f"failed to get fakeid={fakeid} article: {traceback.format_exc()}")
 
        return all_articles
 
    def fetch_articles_by_nickname(self, nickname, begin=0, count=5):
        #通过昵称直接获取文章
        fakeid = self.get_fakeid(nickname, begin, count)
        if not fakeid:
            raise ValueError(f"failed to find account {nickname} fakeid")
        return self.get_articles(fakeid, begin, count)
 
 
def main():
    cookie = "pgv_pvid=1720179070694403; pgv_info=ssid=s1728007067945403; rewardsn=; wxtokenkey=777; ua_id=ccqbYfRVmkuTCKnxAAAAAPXH7p0ZzQGEqf4t5kO6J-Q=; wxuin=26541242614837; mm_lang=zh_CN; pac_uid=0_pz7Wka8Wzd1yK; suid=user_0_pz7Wka8Wzd1yK; _qimei_q32=e32e2bb0ca9fab678ea45392198e1a46; _qimei_q36=f96565d6416edc7d609a7c24300014917801; _qimei_h38=8f95037c40a041a7496f49fe0200000921790b; _qpsvr_localtk=0.22386122186616886; current-city-name=gd; _qimei_fingerprint=5ed7a27652b8ec7b823ab8adf8674e7e; _qimei_uuid42=18c11091b2810030b969a768200a4655c765eba74b; _clck=t8h3sc|1|frs|0; uuid=8b678c025db85bd44c070628be99d50e; bizuin=3875631693; ticket=78312cab92930043c9b10afd1e78e152cb73e0f0; ticket_id=gh_682a5223e002; slave_bizuin=3875631693; cert=zCwKXXYJSkb6973hzN7UnWJzlAZTLMAp; noticeLoginFlag=1; rand_info=CAESIL5Ket5C2FGRcjnrL1aPvynmIaB2MixTdSS6G/315rmd; data_bizuin=3875631693; data_ticket=UssloCkaGT2HRnjklzs5GFhv8f9h8xRyq1cYcJPlZ16Athl8+yydbqtQqqoSHPJp; slave_sid=UmFBY3dJbzdYNzM4MURkVHk3d3JNQlh2V0dMckpNc1pmakR0bXRpb2JVaWROdHJmTm5ScmZFZ2szTlRqSFhCR0d5a3Ryd1lKS2RLeFIyc3BDblllWjA4Q2hWVVVOVnBGaXJfekRmaG53RVd5WWdnNXBNbVMxVUQyb2l6UlpZWlE5SWZJa294TEpQa0RGSEJz; slave_user=gh_682a5223e002; xid=1537745932e671ceb3782c9ba151993f; openid2ticket_oTXIB59nB-tqNzCoRKdrqAA6LydE=khx0zv+/opSYQDiO864dmqknXctVEvZw9s6jlzebJVg=; _clsk=11f87vu|1734414948734|4|1|mp.weixin.qq.com/weheat-agent/payload/record"
    token = "1100786075"  # 需要填入有效的 token
    nickname = "motuofan" 
 
 
    spider = WeChatSpider(cookie, token)
 
    try:
        articles = spider.fetch_articles_by_nickname(nickname)
        if not articles:
            print(f"unable to get {nickname} article")
            return
 
        # 将文章列表转换为 DataFrame
        df = pd.DataFrame(articles)
 
        # 导出到 Excel 文件
        excel_file = fr"C:\Users\yuanj\OneDrive\Desktop\{nickname}_articles.xlsx"
        df.to_excel(excel_file, index=False)  

        print(f"successfully exported {excel_file}")
    except Exception as e:
        print(f"error {e}")
 
 
if __name__ == "__main__":
    main()







