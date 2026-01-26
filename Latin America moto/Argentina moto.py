from bs4 import BeautifulSoup
import urllib.request
import requests
import pandas as pd
import openpyxl
from datetime import datetime
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException



chrome_options = Options()
chrome_options.add_argument("--ignore-certificate-errors")
chrome_options.add_argument("--ignore-ssl-errors")
chrome_options.add_argument("--disable-features=NetworkService")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")  # Helps avoid bot detection


service = Service('C:\Program Files\python chrome\chrome-win\chromedriver.exe')
driver = webdriver.Chrome(service=service,options=chrome_options)

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"
}

url = "https://motoblog.com/asi-fueron-las-ventas-de-motos-en-abril-de-2025/"


try:
    driver.get(url)
    page_source = driver.page_source

except TimeoutException:
    page_source = driver.page_source

soup = BeautifulSoup(page_source, 'html.parser')
print(soup)