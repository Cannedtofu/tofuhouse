
from bs4 import BeautifulSoup
import urllib.request
import requests
from selenium import webdriver
import openpyxl




def read_txt(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    return content


wb = openpyxl.load_workbook("D:\\Arcteryx.xlsx")
sheet = wb["Data"]



for i in range(10,24):
    file_path = fr'D:\麦星\研究\亚玛芬\爬虫\202503\{i}.txt'
    print(file_path)
    content = read_txt(file_path)

    soup = BeautifulSoup(content, "html.parser")
    data = soup.find_all("div", class_="conv-section-store-parent")

    for store in data:
        output = []
        store_name = store.find('h3').text
        store_address = store.find('h5',class_='conv-section-store-address section-subtitle dl-store-address js-store-location').text
        output = [store_name,store_address]

        store_button = store.find('div',class_="conv-section-action-btns")
        store_button_info = store.find_all('a')
        for i in store_button_info:
            output.append(i.get('data-tooltip'))

        print(output)
        
        sheet.append(output)
        wb.save("D:\\Arcteryx.xlsx")

    print('Completed file: '+ file_path)




    
