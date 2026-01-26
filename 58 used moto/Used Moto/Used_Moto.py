
from bs4 import BeautifulSoup
import urllib.request
import requests
import pandas as pd
import openpyxl
from datetime import datetime
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders



def scrap_city(city,city_url):

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"}
    response = requests.get(city_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    items = soup.find_all('a',{'class':"CarItem__CarItemL-smlarw-0 dIxbtd"})
        
    data_to_append = []

    for entry in items:
        #清单页
        item_url= entry.get('href')
        item_url= 'https://www.58moto.com'+item_url
        print(item_url)

        car_name_tag=entry.find('div',{"class":"l-title ellipsis2"})
        car_name=car_name_tag.get_text(strip=True) if car_name_tag else "N/A"
            
        time_milage_where=entry.find('div',{"class":"l-tags ellipsis"})
        TMW = time_milage_where.find_all('span')
        if len(TMW)>=3:
            car_age = TMW[0].text
            car_milage = TMW[1].text
            car_place = TMW[2].text

        car_price_tag = entry.find('div',{"class":"l-bottom"})
        car_price = car_price_tag.get_text(strip=True) if car_price_tag else "N/A"

        time.sleep(0.2)

        #商品页

        response = requests.get(item_url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')

        data = soup.find_all('span',{"class":"ant-descriptions-item-content"})
        sku_data=[]
        
        for i in data:
            sku_data.append(i.text)


        comment = soup.find('div',{"class":"layout__RichContent-sc-10xfiv-17 jlIEJb"})
        if comment is None:
            comment = ""
        else:
            comment=comment.text

        sku_data.append(comment)
        time.sleep(0.1)


        print(city,item_url,car_name,car_age,car_milage,car_place,car_price,sku_data)
        data_to_append.append([city,item_url,car_name,car_age,car_milage,car_place,car_price]+sku_data)

    return data_to_append





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



file_name = "D:\\Used Moto.xlsx"  # Replace with your Excel file name
input_sheet_name = "City"  # Replace with the sheet name where the links are stored
output_sheet_name = "Data"

df = pd.read_excel(file_name, sheet_name=input_sheet_name) 

for index, row in df.iterrows():
    url = row['Url']
    city = row['City']
    print(url)
    data=scrap_city(city,url)
    append_to_excel(file_name,output_sheet_name,data)




    

