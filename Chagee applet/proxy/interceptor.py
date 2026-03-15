import json
from mitmproxy import http

class AppletInterceptor:
    def __init__(self):
        # The specific domain or URL path you want to intercept. 
        # Needs to be configured based on the target applet's API.
        self.target_url_fragment = "api.targetapplet.com/data" 
        self.output_file = "data/scraped_results.json"

    def response(self, flow: http.HTTPFlow):
        # Check if the response URL contains our target fragment
        if self.target_url_fragment in flow.request.pretty_url:
            print(f"Intercepted target API call: {flow.request.pretty_url}")
            
            # Extract JSON data from the response body
            try:
                data = json.loads(flow.response.get_text())
                
                # Save the data to a file
                with open(self.output_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                    
                print(f"Successfully saved data to {self.output_file}")
            except Exception as e:
                print(f"Failed to parse or save JSON from response: {e}")

addons = [
    AppletInterceptor()
]
