class MockTextField:
    def __init__(self):
        self.value = ""

class MockPage:
    """模拟 Flet 页面，测试中忽略 UI 控件操作"""
    def close(self, *args): pass
    def update(self, *args): pass

class MockBatchPostPage:
    def __init__(self):
        self.title_pool = MockTextField()
        self.content_pool = MockTextField()
        self.page = MockPage()
        self.link_dialog = None
    
    def _show_snackbar(self, msg, status):
        print(f"  [提示] {msg}")
        
    def _simulate_on_select(self, link_data):
        """完全复制 batch_post_page.py 当前版本的 on_select 逻辑"""
        code = link_data['shortCode']
        seo_title = link_data.get('seoTitle') or ""
        desc = link_data.get('description') or ""
        
        # 标题池：每次严格追加一行
        effective_title = seo_title if seo_title else f"主页输入【{code}】查看网盘资源"
        
        current_title = self.title_pool.value or ""
        if current_title and not current_title.endswith("\n"):
            current_title += "\n"
        if effective_title not in current_title:
            self.title_pool.value = current_title + effective_title

        # 内容池：每次严格追加一行
        current_val = self.content_pool.value or ""
        if current_val and not current_val.endswith("\n"):
            current_val += "\n"
        
        new_content = f"{desc} 👉 主页输入【{code}】立刻查阅" if desc else f"主页输入【{code}】立刻查阅"
        self.content_pool.value = current_val + new_content
        
        self._show_snackbar(f"✅ 短码 {code} 及 SEO 物料已加载", "success")


def count_lines(text: str) -> int:
    """统计非空行数"""
    return len([l for l in text.splitlines() if l.strip()])


def run_test():
    print("=" * 55)
    print(" 测试：标题池与内容池行数严格 1:1 对齐验证")
    print("=" * 55)
    
    page = MockBatchPostPage()
    
    # 构造包含各种情况的短链数据集
    links = [
        {"shortCode": "pro",    "seoTitle": "高效短链接管理工具推荐",       "description": "短链接管理工具"},
        {"shortCode": "qiNcR9", "seoTitle": "夸克网盘分享，海量资源等你探索", "description": None},  # 无描述
        {"shortCode": "UDplYM", "seoTitle": None,                           "description": None},  # 无标题无描述
        {"shortCode": "vCTTAg", "seoTitle": None,                           "description": "百度资源实时更新"}, # 无标题有描述
    ]
    
    passed = True
    for i, link in enumerate(links):
        code = link['shortCode']
        print(f"\n[{i+1}] 选取短链: {code} | SEO: {link['seoTitle'] or '无'} | 描述: {link['description'] or '无'}")
        page._simulate_on_select(link)
        
        title_lines = count_lines(page.title_pool.value)
        content_lines = count_lines(page.content_pool.value)
        
        match = (title_lines == content_lines)
        status = "✅ 通过" if match else "❌ 失败"
        print(f"  标题池行数={title_lines}, 内容池行数={content_lines} → {status}")
        
        if not match:
            passed = False

    print("\n" + "=" * 55)
    print(f"标题池最终内容:\n{page.title_pool.value}")
    print("-" * 30)
    print(f"内容池最终内容:\n{page.content_pool.value}")
    print("=" * 55)
    print("✅ 全部测试通过！" if passed else "❌ 存在测试失败，请检查对齐逻辑！")

if __name__ == "__main__":
    run_test()
