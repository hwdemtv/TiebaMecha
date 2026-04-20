import sys

# Read the file
with open('/home/hw/TiebaMecha/src/tieba_mecha/web/pages/survival.py', 'r') as f:
    content = f.read()

# Add debug print in load_data
old = '''    async def load_data(self):
        """加载数据"""
        if not self.db:
            return'''

new = '''    async def load_data(self):
        """加载数据"""
        import sys
        sys.stderr.write("[SURVIVAL] load_data called, db={}\\n".format(self.db))
        if not self.db:
            return'''

content = content.replace(old, new)

# Add debug print in build
old2 = '''    def build(self) -> ft.Control:
        """构建页面"""'''

new2 = '''    def build(self) -> ft.Control:
        """构建页面"""
        import sys
        sys.stderr.write("[SURVIVAL] build called, stats={}\\n".format(self._stats))
        sys.stderr.write("[SURVIVAL] account_options count={}\\n".format(len(self._account_options)))'''

content = content.replace(old2, new2)

# Write back
with open('/home/hw/TiebaMecha/src/tieba_mecha/web/pages/survival.py', 'w') as f:
    f.write(content)

print("Debug added successfully")
