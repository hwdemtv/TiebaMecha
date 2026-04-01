import os
import sys

# 设置修复的环境变量
os.environ['LOG_LEVEL'] = 'warning'
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'

sys.path.insert(0, 'src')

print('测试环境变量设置:')
log_level = os.environ.get('LOG_LEVEL')
print(f'LOG_LEVEL={log_level}')

try:
    # 尝试导入相关模块
    import uvicorn
    import flet as ft
    from tieba_mecha.web.app import run_app
    
    print('OK: 所有必要的模块导入成功')
    
    # 测试 uvicorn 配置
    config = uvicorn.Config(app=None, host='127.0.0.1', port=9006, log_level='warning')
    print('OK: uvicorn 配置成功 (log_level="warning")')
    
    print('\n修复验证完成！')
    print('现在可以运行: python start_web_simple.py')
    
except ImportError as e:
    print(f'ERROR: 导入失败: {e}')
    print('请安装依赖: pip install flet>=0.21.0 uvicorn')
except Exception as e:
    print(f'ERROR: 测试失败: {e}')
    import traceback
    traceback.print_exc()