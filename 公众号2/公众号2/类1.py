#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
@file:test4.py
@time:2022/12/28
"""
import time

import requests
import pandas as pd


def getMoreInfo(link):
    # 获得mid,_biz,idx,sn 这几个在link中的信息
    mid = link.split("&")[1].split("=")[1]
    idx = link.split("&")[2].split("=")[1]
    sn = link.split("&")[3].split("=")[1]
    _biz = link.split("&")[0].split("_biz=")[1]

    # fillder 中取得一些不变得信息
    # req_id = "0614ymV0y86FlTVXB02AXd8p"
    pass_ticket = "mow9VwogWBWPS9AO3J2DiBQGU7Wa4sTTIh+vCkP0Fag3HfVd/YGQFnPPPQew2cZy"  # 从fiddler中获取
    appmsg_token = "1301_MTVZpf9Iavqmp9gQ7Zeg7_Nrm862oqU4wyAlAaZA3ASPK9G0XdXEBgAQBjUq_FdL838n_Blz7sCp4K5M"  # 从fiddler中获取
    uin = "MjUwNTY3NTc1" # 从fiddler 中获取
    key = "daf9bdc5abc4e8d040a0ed40abced512aa960ba1e69b11ad0168337589ed5210256038e2dd28dc78dd672e47f185668bc0aa6493148ebc0454314e03b34e7bc6f1da93f95414ec224b658612abd7001ddf03184134622d21f3b3040595f8133e87078be8c4a07746f40d122ec32cd07aaf0def1b151d70eeff94ce86d7999741" # 从fiddler 中获取

    # 目标url
    url = "https://mp.weixin.qq.com/s/Dews_s-VNmcEVOICl3rAcg"  # 获取详情页的网址
    # 添加Cookie避免登陆操作，这里的"User-Agent"最好为手机浏览器的标识
    phoneCookie = "rewardsn=; wxtokenkey=777; wxuin=250567575; devicetype=Windows11x64; version=63090c11; lang=zh_CN; appmsg_token=1301_AH6X8DeevqMzzpSm7Zeg7_Nrm862oqU4wyAlAXkmA0OaOE3dktWoVCMD9xpvqzhoWCqLUYlD4Y7IWJ5-; pass_ticket=auX/xA11Qnw4ztTnD32wB36mRFzfSKRynZvjN8GgCGVgmHy21OxW/YXwkK6IDXBb; wap_sid2=CJe3vXcSigF5X0hPOGs1VmpscW56MWlBMzRLeEM5MUpWXzk5TXpKYzk2MjR2WC01VkRacGJQTl9yUU44Wi00ckxEMTFweGNjY2VMUi12U3Jnb0k3ZENLWDVfWUFPb0o4Y3VGOXBDR292SnUtTFlMeEdZMkNtdDloMXMxaU0yazhpbGQ5cFoyTEhjTnZJU0FBQX4wvKuLuwY4DUAB" # 从fiddler 中获取 
    headers = {
        "Cookie": phoneCookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090c11) XWEB/11581 Flue"
}
    # 添加data，`req_id`、`pass_ticket`分别对应文章的信息，从fiddler复制即可。
    data = {
        "is_only_read": "1",
        "is_temp_url": "0",
        "appmsg_type": "9",
        'reward_uin_count': '0'
    }
    """
    添加请求参数
    __biz对应公众号的信息，唯一
    mid、sn、idx分别对应每篇文章的url的信息，需要从url中进行提取
    key、appmsg_token从fiddler上复制即可
    pass_ticket对应的文章的信息，也可以直接从fiddler复制
    """
    params = {
        "__biz": _biz,
        "mid": mid,
        "sn": sn,
        "idx": idx,
        "key": key,
        "pass_ticket": pass_ticket,
        "appmsg_token": appmsg_token,
        "uin": uin,
        "wxtoken": "777",
    }

    content = requests.post(url, headers=headers, data=data, params=params).json()
    # 提取其中的阅读数和点赞数
    print(content)

    print(content["appmsgstat"]["read_num"], content["appmsgstat"]["like_num"])
    try:
        readNum = content["appmsgstat"]["read_num"]
        print("readnum:" + str(readNum))
    except:
        readNum = 0
    try:
        likeNum = content["appmsgstat"]["like_num"]
        print("likenum:" + str(likeNum))
    except:
        likeNum = 0
    try:
        old_like_num = content["appmsgstat"]["old_like_num"]
        print("readingnum:" + str(old_like_num))
    except:
        old_like_num = 0

    return readNum, likeNum, old_like_num






url = "http://mp.weixin.qq.com/s?__biz=MzU3Nzg0MDg2Nw==&mid=2247593769&idx=1&sn=cfc4f34bd80ace0ff38b0909ba91ba48&chksm=fd7d5ca5ca0ad5b329e60967341a5847e415b2f587deddb6ea84f11899479e9e7dfc92a339f4#rd" 

phoneCookie= "pgv_pvid=1720179070694403; pgv_info=ssid=s1728007067945403; rewardsn=; wxtokenkey=777; ua_id=ccqbYfRVmkuTCKnxAAAAAPXH7p0ZzQGEqf4t5kO6J-Q=; wxuin=26541242614837; mm_lang=zh_CN; pac_uid=0_pz7Wka8Wzd1yK; suid=user_0_pz7Wka8Wzd1yK; _qimei_q32=e32e2bb0ca9fab678ea45392198e1a46; _qimei_q36=f96565d6416edc7d609a7c24300014917801; _qimei_h38=8f95037c40a041a7496f49fe0200000921790b; _qpsvr_localtk=0.22386122186616886; current-city-name=gd; _qimei_fingerprint=5ed7a27652b8ec7b823ab8adf8674e7e; _qimei_uuid42=18c11091b2810030b969a768200a4655c765eba74b; uuid=8b678c025db85bd44c070628be99d50e; bizuin=3875631693; ticket=78312cab92930043c9b10afd1e78e152cb73e0f0; ticket_id=gh_682a5223e002; slave_bizuin=3875631693; cert=zCwKXXYJSkb6973hzN7UnWJzlAZTLMAp; noticeLoginFlag=1; rand_info=CAESIL5Ket5C2FGRcjnrL1aPvynmIaB2MixTdSS6G/315rmd; data_bizuin=3875631693; data_ticket=UssloCkaGT2HRnjklzs5GFhv8f9h8xRyq1cYcJPlZ16Athl8+yydbqtQqqoSHPJp; slave_sid=UmFBY3dJbzdYNzM4MURkVHk3d3JNQlh2V0dMckpNc1pmakR0bXRpb2JVaWROdHJmTm5ScmZFZ2szTlRqSFhCR0d5a3Ryd1lKS2RLeFIyc3BDblllWjA4Q2hWVVVOVnBGaXJfekRmaG53RVd5WWdnNXBNbVMxVUQyb2l6UlpZWlE5SWZJa294TEpQa0RGSEJz; slave_user=gh_682a5223e002; xid=1537745932e671ceb3782c9ba151993f; openid2ticket_oTXIB59nB-tqNzCoRKdrqAA6LydE=khx0zv+/opSYQDiO864dmqknXctVEvZw9s6jlzebJVg=; _clck=3875631693|1|frt|0; _clsk=1osk554|1734530199117|2|1|mp.weixin.qq.com/weheat-agent/payload/record"

headers = {
        "Cookie": phoneCookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090c11) XWEB/11581 Flue"
}

content = requests.post(url, headers=headers).json()
print(content)



#readNum, likeNum, old_like_num = getMoreInfo(url)

#print(readNum, likeNum, old_like_num)
# 歇3s，防止被封
time.sleep(3)
