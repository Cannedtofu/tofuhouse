# Guming Applet Scraper: Global Settings

# --- Target City List ---
# Format: (City Name, Target Store Count, Pinyin / Unused Placeholder, Sub-regions List)
CITY_LIST = [
    ('宁波', 300, 'xxx', ['海曙区', '江北区', '镇海区', '北仑区', '鄞州区', '奉化区']),
    ('杭州', 300, 'xxx', ['上城区', '拱墅区', '西湖区', '滨江区', '萧山区', '余杭区', '临平区', '钱塘区', '富阳区', '临安区']),
    ('泉州', 200, 'xxx', ['鲤城区', '丰泽区', '洛江区', '泉港区', '晋江市', '石狮市', '南安市']),
    ('金华', 200, 'xxx', ['婺城区', '金东区', '义乌市', '东阳市', '永康市', '兰溪市']),
    ('广州', 200, 'xxx', ['越秀区', '海珠区', '荔湾区', '天河区', '白云区', '黄埔区', '花都区', '番禺区', '南沙区', '从化区', '增城区']),
    ('重庆', 200, 'xxx', ['万州区', '涪陵区', '渝中区', '大渡口区', '沙坪坝区', '九龙坡区', '南岸区', '北碚区', '巴南区', '长寿区', '江津区', '合川区', '永川区', '綦江区', '大足区', '璧山区', '铜梁区', '潼南区', '荣昌区', '开州区']),
    ('赣州', 150, 'xxx', ['章贡区', '南康区', '赣县区', '瑞金市', '龙南市']),
    ('惠州', 100, 'xxx', ['惠城区', '惠阳区'])
]
DEFAULT_TARGET_COUNT = 200

# --- City Selection Coordinates (Relative to Applet Window) ---
# Coordinates to open the city search selector page
# Can click "上海市 >" at (45, 152) or the yellow button "查找其他城市门店" at (208, 558)
CITY_SELECTOR_TRIGGER = (45, 152)
CITY_SELECTOR_YELLOW_BTN = (208, 558)

# Coordinate for the search city input box in the Region Select page
CITY_SEARCH_INPUT_COORD = (208, 92)

# --- Store List Interaction ---
# Tab coordinate for "点单" (second tab at bottom) to enter the store list
STORE_LIST_ENTRY_REL_COORD = (163, 748)

# Coordinate to collapse the Tencent Map widget to reveal more store cards
MAP_COLLAPSE_COORD = (200, 433)
MAP_COLLAPSE_KEYWORD = "收起地图"

# Hover coordinate inside store list for scrolling
SCROLL_REL_COORD = (200, 500)
SCROLL_WHEEL_TIMES = 4
MAX_NO_NEW_SCROLLS = 3

# --- OCR Processing & Bounding Boxes ---
# Store Box detection height and width constraints
BOX_MIN_WIDTH = 320
BOX_MAX_WIDTH = 450
BOX_MIN_HEIGHT = 160
BOX_MAX_HEIGHT = 300

# Region of Interest: ignore top 280px (header, tabs, search bar, map collapse button)
# In 430x788 resolution, 280px is ~35% of screen height
ROI_TOP_IGNORE_HEIGHT = 280

# --- Threshold & Screenshots ---
SCREENSHOT_ON_THRESHOLD = True
CUP_COUNT_THRESHOLD = 100       # Highlight if cup count >= this
DATA_FOLDER = "data"

# --- Email Reporting Configuration ---
SEND_EMAIL = True
SENDER_EMAIL = '396481139@qq.com'
SENDER_PASSWORD = 'mocjzkhznmudbghf'
RECEIVER_EMAIL = 'cuiyuan@maisoncapital.com, 396481139@qq.com, linhuaqiang@maisoncapital.com, zengleshi@maisoncapital.com, wangziyuan@maisoncapital.com'
