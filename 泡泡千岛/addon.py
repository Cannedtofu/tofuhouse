import json
import os
from mitmproxy import http

class DataInterceptor:
    def __init__(self, target_url, output_file):
        self.target_url = target_url
        self.output_file = output_file

    def response(self, flow: http.HTTPFlow):
        # 过滤目标接口
        if self.target_url in flow.request.pretty_url:
            content = flow.response.get_text()
            try:
                data = json.loads(content)
                # 以追加模式写入 JSONL 格式
                with open(self.output_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
            except Exception as e:
                pass # 忽略非 JSON 或解析错误

addons = [DataInterceptor("api.target_domain.com", "results.jsonl")]