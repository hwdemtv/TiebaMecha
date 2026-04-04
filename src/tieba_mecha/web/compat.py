"""
Flet 版本兼容性层

处理不同 Flet 版本之间的 API 差异，确保代码在各版本下都能正常运行。
"""

import logging

# ============================================================
# Uvicorn 日志级别修复
# Flet 可能传递 'warn'，但 uvicorn 只接受 'warning'
# ============================================================

def patch_uvicorn_log_level():
    """修复 uvicorn 日志级别兼容性问题"""
    try:
        import uvicorn.config as _uvc
        _orig_configure_logging = _uvc.Config.configure_logging

        def _patched_configure_logging(self):
            if getattr(self, "log_level", None) == "warn":
                self.log_level = "warning"
            return _orig_configure_logging(self)

        _uvc.Config.configure_logging = _patched_configure_logging
    except (ImportError, AttributeError):
        pass


# ============================================================
# 日志级别名称标准化
# Flet/aiotieba 可能覆盖 logging.WARNING 的名称
# ============================================================

def normalize_log_levels():
    """恢复标准日志级别名称"""
    logging.addLevelName(logging.WARNING, "WARNING")
    logging.addLevelName(logging.DEBUG, "DEBUG")
    logging.addLevelName(logging.INFO, "INFO")
    logging.addLevelName(logging.ERROR, "ERROR")


# ============================================================
# 启动时自动应用所有补丁
# ============================================================

def apply_all_patches():
    """应用所有兼容性补丁"""
    patch_uvicorn_log_level()
    normalize_log_levels()


# 在模块导入时自动执行
apply_all_patches()
