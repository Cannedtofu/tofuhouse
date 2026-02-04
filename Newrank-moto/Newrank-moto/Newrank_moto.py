
# coding=gbk

from bs4 import BeautifulSoup
import urllib.request
from urllib import request
import time, datetime, subprocess, random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
import pandas as pd
from openpyxl import load_workbook
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import datetime


#delay = random.uniform(0, 600)
#time.sleep(delay)

def sleep_random_second(num):
    while num < 5:
    	# random.uniform可以实现沉睡0.01-0.001秒，需要round来保证有效数字
        time.sleep(round(random.uniform(1, 0.5), 3))
        print("========================", round(random.uniform(1,0.5), 3))
        num = num + 1

url='https://www.newrank.cn/new?account=imotofine'

# Add custom user-agent
options = Options()
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Initialize WebDriver
service = Service("D:\爬虫用Chrome\chrome-win\chromedriver.exe")
driver = webdriver.Chrome(service=service, options=options)
driver.get(url)






element = driver.find_element(By.CLASS_NAME, "ant-spin-container") #找到登录键
element.click()

element = driver.find_element(By.CLASS_NAME, "_2XRFN1F6") #其他登录方式
element.click()

# Find the login elements
username_input = driver.find_element(By.XPATH, "//input[@placeholder='手机 / 邮箱 / 新榜ID']")
password_input = driver.find_element(By.XPATH, "//input[@placeholder='输入密码']")
# Find the login button using its class
login_button = driver.find_element(By.CSS_SELECTOR, "button._3RtjFeM-._CH1sF8Xz._38DPDVRd")
login_keep = driver.find_element(By.CLASS_NAME, "nrd-login-checkbox-input")


username_input.send_keys("13670076001")
password_input.send_keys("usXjgvAvx@JBe82")
login_keep.click()

login_button.click()

# Wait for the slider to appear
slider_handle = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "nc_1_n1z")))  # Slider handle's ID

# Simulate dragging the slider handle to the right
action = ActionChains(driver)

# Get the initial position of the slider handle and drag it
action.click_and_hold(slider_handle).move_by_offset(300, 0).release().perform()  # Adjust the offset as necessary