import asyncio
from tieba_mecha.db.crud import Database
from tieba_mecha.web.pages.survival import SurvivalPage

class MockPage:
    def update(self):
        pass

async def main():
    try:
        db = Database()
        await db.init_db()
        
        page = MockPage()
        survival_page = SurvivalPage(page, db)
        
        print("Building page...")
        control = survival_page.build()
        print("Page built successfully.", type(control))
        
        print("Loading data...")
        await survival_page.load_data()
        print("Data loaded successfully.")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
