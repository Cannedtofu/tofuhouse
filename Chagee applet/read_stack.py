import codecs

with codecs.open('test_output.txt', 'r', 'utf-16le') as f:
    text = f.read()

print("\n==== STACK TRACE START ====\n")
print(text)
print("\n==== STACK TRACE END ====\n")
