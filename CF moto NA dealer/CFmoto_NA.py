import requests
from lxml import html

def scrape_cfmoto_dealers():
    """
    Scrapes dealer information from the CFMoto USA website.
    """
    url = "https://www.cfmotousa.com/dealer-locator"
    xpath = "/html/body/div[4]/div[4]/div/div[3]/div/div/div/div/div[1]/ul"

    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes

        tree = html.fromstring(response.content)
        
        dealer_list = tree.xpath(xpath)

        if not dealer_list:
            print("Could not find the dealer list element using the provided XPath.")
            print("The website structure might have changed.")
            return

        ul_element = dealer_list[0]
        
        for li in ul_element.xpath('./li'):
            name = li.xpath('.//h4/text()')
            name = name[0].strip() if name else 'N/A'

            address_parts = li.xpath('.//p[1]/text()')
            address = ' '.join([part.strip() for part in address_parts if part.strip()])
            
            phone = li.xpath('.//p[2]/a/text()')
            phone = phone[0].strip() if phone else 'N/A'

            print(f"Dealer: {name}")
            print(f"Address: {address}")
            print(f"Phone: {phone}")
            print("-" * 20)

    except requests.exceptions.RequestException as e:
        print(f"Error fetching the URL: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    scrape_cfmoto_dealers()
