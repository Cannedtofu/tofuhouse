
from bs4 import BeautifulSoup
import urllib.request
import requests
from urllib import request
import time, datetime, subprocess, random
from selenium import webdriver
from selenium.webdriver.common.by import By
import pandas as pd
from openpyxl import load_workbook
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import datetime






def scrap_city(dealer_url):

    driver.get(dealer_url)
    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'html.parser')

    try:
        dealer_detail_address = soup.find('address').text
    except:
        print('fail to get brands')
        dealer_detail_address=""
    try:
        dealer_brands= soup.find('div', class_='multiple-brands-support margin-top-sm').text
    except:
        print('fail to get brands')
        dealer_brands=""

    data=[dealer_url,dealer_detail_address,dealer_brands]
    print(dealer_url,dealer_detail_address,dealer_brands)
    time.sleep(0.1)

    
    print(data)



    return data

def append_to_excel(file_name,sheet_name,data_to_append):
    try:
        workbook = openpyxl.load_workbook(file_name)
        sheet = workbook[sheet_name]
        for row in data_to_append:
            sheet.append(row)

        workbook.save(file_name)
        print(f"Data appended successfully to {file_name} in sheet '{sheet_name}'.")

    except Exception as e:
        print("failed to input data")

def clean_info(info_list):
    return [item.replace("  ", "").replace("\n", "") for item in info_list]

def scrap_article(article_url):
    response = requests.get(article_url)
    if response.status_code == 200:
        # Parse the HTML content using BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
    
        # Print the title of the page as an example
        print(soup.title.string)
    else:
        print(f"Failed to retrieve the page.{article_url} Status code: {response.status_code}")



file_name = "D:\\Moto Wechat.xlsx"  # Replace with your Excel file name
input_sheet_name = "url"  # Replace with the sheet name where the links are stored
output_sheet_name = "Data"

df = pd.read_excel(file_name, sheet_name=input_sheet_name)

#service = Service('C:\Program Files\python chrome\chrome-win\chromedriver.exe')
#driver = webdriver.Chrome(service=service)



for index, row in df.iterrows():
    article_url = row[0]
    article_name = row[1]
    print(article_url)

    type(article_url)



    
    #data_to_append = 
    #append_to_excel("D:\\Moto Wechat.xlsx",output_sheet_name,data_to_append)




    

#for index, row in df.iterrows():
#    url = row['Url']
#    city = row['City']
#    print(url)
#    data=scrap_city(city,url)
#    append_to_excel(file_name,output_sheet_name,data)




    

