from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

options = Options()
options.add_argument("--ignore-certificate-errors")
options.add_argument("--start-maximized")
options.add_argument("--ignore-ssl-errors")
options.add_argument("--disable-web-security")

# Suppress Chrome's own noisy logs
options.add_experimental_option("excludeSwitches", ["enable-logging"])
options.add_argument("--log-level=3")  # 0=ALL, 1=INFO, 2=WARNING, 3=ERROR

service = Service(ChromeDriverManager().install())
service.log_path = "NUL"  # discard chromedriver logs (use "/dev/null" on Linux/macOS)

driver = webdriver.Chrome(service=service, options=options)



# --- Set page load timeout ---
driver.set_page_load_timeout(10)  # stop loading after 10 seconds

driver.get("https://www.polaris.com/en-us/off-road/dealers/alabama/andalusia/2007200/")
print("Title:", driver.title)

driver.quit()
