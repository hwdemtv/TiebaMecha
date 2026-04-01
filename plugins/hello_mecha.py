"""Example plugin for TiebaMecha"""

def setup():
    """插件初始化函数 (必须)"""
    print("[Plugin: Example] 初始化插件...")
    return True

async def run(db, client=None):
    """插件执行函数 (可选)"""
    print("[Plugin: Example] 运行插件成功!")
    # 这里可以进行自定义数据库操作或贴吧操作
    return {"status": "success", "msg": "Hello from Plugin!"}
