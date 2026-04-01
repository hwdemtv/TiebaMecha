"""调试导入错误的临时脚本"""
import sys
import traceback

sys.path.insert(0, "src")

try:
    # 逐层检查导入
    print("检查 models...")
    from tieba_mecha.db.models import BatchPostTask
    print("BatchPostTask OK:", BatchPostTask.__table__.columns.keys())
except Exception as e:
    print("models 导入失败:")
    traceback.print_exc()
