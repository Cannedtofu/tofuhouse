
from bs4 import BeautifulSoup
import urllib.request
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import openpyxl


# Read HTML source from file
with open(r"C:\Users\yuanj\OneDrive\Desktop\CF moto.txt", "r", encoding="utf-8") as file:
    page_source = file.read()

soup = BeautifulSoup(page_source, "html.parser")


data= soup.find("ul", id="lad__location-list", class_="lad__loc-list lad__location-list lad__loc-list--top")
#dealers= data.find_all('li', style="display: none;",attrs={"data-id": True})
dealers= data.find_all('li',attrs={"data-id": True})

wb = openpyxl.load_workbook("D:\\CF moto dealers.xlsx")
sheet = wb["Data"]



for n in dealers:
    data=[]
    moto = atv = sbs = website = ''
    dealer_name = n.find('h4').text
    address = n.find('span',{'class','address'}).text
    website_tag = n.find('span', {'class': 'website'})
    if website_tag:
        # If the website span is found, try to get the 'href' from the <a> tag inside it
        website_link = website_tag.find('a')
        if website_link:
            website = website_link.get('href', '')  # If 'href' exists, get it; else return empty string
    else:
        # If no 'span' with class 'website' is found, website remains an empty string
        website = ''

    brands = n.find('ul', {'class': 'carried-brands'}).find_all('li') if n and n.find('ul', {'class': 'carried-brands'}) else []

    for brand in brands:
        # Check if the class matches for each <li> and extract the text
        if 'MCY' in brand.get('class', []):
            moto = brand.get_text(strip=True)
        elif 'ATV' in brand.get('class', []):
            atv = brand.get_text(strip=True)
        elif 'SXS' in brand.get('class', []):
            sbs = brand.get_text(strip=True)
    

    print(dealer_name,address,website,moto,atv,sbs)
    sheet.append([website,address,"","",dealer_name,"","","","","","",moto,atv,sbs])

wb.save("D:\\CF moto dealers.xlsx")

    
    