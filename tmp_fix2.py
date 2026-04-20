import sys

# Read the file
with open('d:/软件开发/TiebaMecha/src/tieba_mecha/web/pages/survival.py', 'r') as f:
    content = f.read()

# Replace the load_data completion section to rebuild the page
old = '''    async def load_data(self):
        """加载数据"""
        import sys
        sys.stderr.write("[SURVIVAL] load_data called, db={}%s\\\\n".format(self.db))
        if not self.db:
            return
        try:
            self._stats = await self.db.get_survival_stats()
            accounts = await self.db.get_accounts()
            self._account_options = [
                ft.dropdown.Option(str(a.id), a.name or f"账号{a.id}")
                for a in accounts
            ]
            await self._load_page(1)
        except Exception as e:
            import traceback
            traceback.print_exc()'''

new = '''    async def load_data(self):
        """加载数据"""
        import sys
        sys.stderr.write("[SURVIVAL] load_data called, db={}%s\\\\n".format(self.db))
        if not self.db:
            return
        try:
            self._stats = await self.db.get_survival_stats()
            accounts = await self.db.get_accounts()
            self._account_options = [
                ft.dropdown.Option(str(a.id), a.name or f"账号{a.id}")
                for a in accounts
            ]
            await self._load_page(1)
            # 数据加载完成后重新构建页面 UI
            self.content_area.content = self.build()
            self.page.update()
        except Exception as e:
            import traceback
            traceback.print_exc()'''

content = content.replace(old, new)

# Remove the debug from build
old2 = '''    def build(self) -> ft.Control:
        """构建页面"""
        import sys
        sys.stderr.write("[SURVIVAL] build called, stats={}\\n".format(self._stats))
        sys.stderr.write("[SURVIVAL] account_options count={}\\n".format(len(self._account_options)))'''

new2 = '''    def build(self) -> ft.Control:
        """构建页面"""'''

content = content.replace(old2, new2)

# Write back
with open('d:/软件开发/TiebaMecha/src/tieba_mecha/web/pages/survival.py', 'w') as f:
    f.write(content)

print("Fix applied successfully")
