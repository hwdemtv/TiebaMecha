import chardet

file_path = r'D:\软件开发\TiebaMecha\网盘精准配对导入模板.csv'
with open(file_path, 'rb') as f:
    rawdata = f.read()
    result = chardet.detect(rawdata)
    encoding = result['encoding']
    confidence = result['confidence']

print(f"Detected encoding: {encoding} with confidence {confidence}")
print(f"First 50 bytes: {rawdata[:50]}")
try:
    print(f"Decoded with utf-8: {rawdata.decode('utf-8')[:50]}")
except Exception as e:
    print(f"UTF-8 decode failed: {e}")

try:
    print(f"Decoded with gbk: {rawdata.decode('gbk')[:50]}")
except Exception as e:
    print(f"GBK decode failed: {e}")
