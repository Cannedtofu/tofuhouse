import requests

url = "https://prod-na-api.popmart.com/shop/v1/store/mapStoreList"

payload = {
    "lat": 40.7128,
    "lng": -74.0060,
    "zoom": 4,
    "country": "US",
}

headers = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.popmart.com",
    "Referer": "https://www.popmart.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "X-Client-Country": "US",
    "X-Client-Namespace": "america",
    "X-Project-ID": "naus",
    "X-Device-OS-Type": "web",
}

response = requests.post(url, json=payload, headers=headers)
print(response.status_code)
print(response.text)