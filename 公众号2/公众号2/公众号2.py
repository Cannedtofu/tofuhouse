
#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
@file:GzhSpider.py
@time:2022/12/28
"""
import time
from time import sleep

import requests
import pandas as pd
import json


class GzhSpider(object):
    def __init__(self):
        self.token = "25351****"
        self.fakeid = "MzA4MzYwNTA0Mg=="
        self.cookie = "" 

    def get_html(self, page):
        """
        通过微信公众号后台获取数据
        :param page: 页码
        :return:
        """
        params = {
            "action": "list_ex",
            "fakeid": self.fakeid,
            "query": "",
            "begin": str(page * 4),
            "count": "4",
            "type": "9",
            "need_author_name": "1",
            "token": self.token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1"
        }
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36 Edg/108.0.1462.54',
            "cookie": self.cookie
        }
        response = requests.get(url, headers=headers, params=params)
        return response.text

    def parse_data(self, items):
        results = []
        items = json.loads(items)
        if "app_msg_list" not in items:
            return None
        for item in items["app_msg_list"]:
            create_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["create_time"]))
            readNum, likeNum, old_like_num = (0, 0, 0)
            results.append({
                "title": item['title'],
                "url": item['link'],
                "create_time": create_time,
                "author_name": item["author_name"],
                "readNum": readNum,
                "likeNum": likeNum,
                "old_like_num": old_like_num
            })
        print(json.dumps(results, indent=4))
        return results

    def save(self, results):
        data = pd.DataFrame(results)
        data.to_csv("data.csv")

    def run(self):
        results = []
        for i in range(25):  # 采集25页
            html = self.get_html(i)
            result = self.parse_data(html)
            results.extend(result)
            sleep(5)
        self.save(results)


if __name__ == '__main__':
    GzhSpider().run()