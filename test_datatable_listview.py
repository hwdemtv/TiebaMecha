"""测试 ListView 中 DataTable 更新问题"""
import flet as ft

def main(page: ft.Page):
    page.title = "DataTable in ListView Test"

    # 模拟 _refresh_material_table 的方式
    materials_data = []
    counter = [0]

    # 创建 DataTable
    table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID")),
            ft.DataColumn(ft.Text("Title")),
            ft.DataColumn(ft.Text("Status")),
        ],
        rows=[],
    )

    # 包裹在 ListView 中 (模拟 _build_material_view)
    listview = ft.ListView(
        [table],  # DataTable 直接作为 ListView 的第一个元素
        expand=True,
    )

    def add_material(e):
        counter[0] += 1
        materials_data.append({
            "id": counter[0],
            "title": f"Title {counter[0]}",
            "status": "pending"
        })
        refresh_table()

    def refresh_table():
        # 模拟 _refresh_material_table
        new_rows = []
        for m in materials_data:
            new_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(m["id"]))),
                        ft.DataCell(ft.Text(m["title"])),
                        ft.DataCell(ft.Text(m["status"])),
                    ]
                )
            )
        table.rows = new_rows
        print(f"Table rows: {len(table.rows)}, data: {len(materials_data)}")

        # 尝试不同的更新方式
        if listview.page:
            listview.update()
            print("Updated listview")
        page.update()
        print("Updated page")

    page.add(
        ft.ElevatedButton("Add Material", on_click=add_material),
        ft.Container(
            content=listview,
            expand=True,
            border=ft.border.all(1, "blue"),
        ),
    )

    # 初始刷新
    refresh_table()

ft.app(target=main)
