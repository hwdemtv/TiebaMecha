"""Plugin loader for TiebaMecha"""
import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, Any, List

class PluginManager:
    """插件管理器"""
    
    def __init__(self, plugins_dir: str | Path | None = None):
        if plugins_dir is None:
            plugins_dir = Path(__file__).parent.parent.parent.parent / "plugins"
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.plugins: Dict[str, Any] = {}

    def load_plugins(self) -> List[str]:
        """扫描并加载所有插件"""
        loaded_names = []
        plugins_path = Path(self.plugins_dir)
        for file in plugins_path.glob("*.py"):
            if file.name.startswith("_"):
                continue
            
            plugin_name = file.stem
            try:
                # ---------------- AST Sandbox Check ----------------
                import ast
                with open(file, 'r', encoding='utf-8') as f:
                    code = f.read()
                
                tree = ast.parse(code)
                restricted_modules = {'os', 'sys', 'subprocess', 'shutil', 'socket', 'urllib', 'requests'}
                restricted_funcs = {'eval', 'exec', 'open', '__import__'}
                
                is_safe = True
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.split('.')[0] in restricted_modules:
                                print(f"[Plugin Sandbox] ⛔ 警告: 插件 {plugin_name} 涉及高危模块 ({alias.name})，触发沙箱拦截！")
                                is_safe = False
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.split('.')[0] in restricted_modules:
                            print(f"[Plugin Sandbox] ⛔ 警告: 插件 {plugin_name} 涉及高危引入 (from {node.module})，触发沙箱拦截！")
                            is_safe = False
                    elif isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name) and node.func.id in restricted_funcs:
                            print(f"[Plugin Sandbox] ⛔ 警告: 插件 {plugin_name} 包含敏感调用 ({node.func.id})，触发沙箱拦截！")
                            is_safe = False

                if not is_safe:
                    continue  # 抛弃不安全的插件
                # --------------------------------------------------

                spec = importlib.util.spec_from_file_location(plugin_name, file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[plugin_name] = module
                    spec.loader.exec_module(module)
                    
                    # 检查是否有 setup 函数
                    if hasattr(module, "setup"):
                        self.plugins[plugin_name] = module
                        loaded_names.append(plugin_name)
                        print(f"[Plugin] 已加载插件: {plugin_name}")
            except Exception as e:
                print(f"[Plugin] 加载插件 {plugin_name} 失败: {e}")
        
        return loaded_names

    async def run_plugin(self, name: str, *args, **kwargs):
        """运行指定插件的 run 函数"""
        if name in self.plugins and hasattr(self.plugins[name], "run"):
            return await self.plugins[name].run(*args, **kwargs)
        return None

# 全局插件管理器实例
_manager: PluginManager | None = None

def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
