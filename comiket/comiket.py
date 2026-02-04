
from bs4 import BeautifulSoup
import urllib.request
import requests
from selenium import webdriver
import openpyxl



file_path = r'D:\comiket.txt'
with open(file_path, 'r', encoding='utf-8') as file:
    content = file.read()


soup = BeautifulSoup(content, "html.parser")


dealers= soup.find_all('div',class_="age")


wb = openpyxl.load_workbook("D:\\comiket.xlsx")
sheet = wb["Data"]



for tmp in dealers:
    j = tmp.find_all('div', class_="comiket")
    year = tmp.find('h2').text


    for n in j:
        data=[]
        audience = publisher = club = male = female = date = event = ''
        try:
            audience = n.find('div', class_="participant").text if n.find('div', class_="participant") else ""
            publisher = n.find('div', class_="corporation").text if n.find('div', class_="corporation") else ""
            club = n.find('div', class_="circle").text if n.find('div', class_="circle") else ""
            male = n.find('div', class_="men").text if n.find('div', class_="men") else ""
            female = n.find('div', class_="women").text if n.find('div', class_="women") else ""
            date = n.find('div', class_="date").text if n.find('div', class_="date") else ""
            event = n.find('div', class_="title").text if n.find('div', class_="title") else ""
        except Exception as e:
            print(f"An error occurred: {e}")
        

        print(audience,publisher,club,male,female,date,year,event)
        sheet.append([audience,publisher,club,male,female,date,year,event])

wb.save("D:\\comiket.xlsx")


    
    