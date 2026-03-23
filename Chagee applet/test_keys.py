import uiautomation as auto
try:
    auto.SendKeys('{Back}')
    print("Back works")
except Exception as e:
    print("Back error:", e)
try:
    auto.SendKeys('{Backspace}')
    print("Backspace works")
except Exception as e:
    print("Backspace error:", e)
try:
    auto.SendKeys('{BackSpace}')
    print("BackSpace works")
except Exception as e:
    print("BackSpace error:", e)
