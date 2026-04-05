"""测试 DataTable 更新问题"""
import flet as ft

def main(page: ft.Page):
    page.title = "DataTable Update Test"

    # 创建 DataTable
    table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID")),
            ft.DataColumn(ft.Text("Name")),
        ],
        rows=[],
    )

    # 包裹在 ListView 中
    listview = ft.ListView([table], expand=True)

    counter = [0]  # 使用列表保存计数器

    def add_row(e):
        counter[0] += 1
        table.rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(str(counter[0]))),
                    ft.DataCell(ft.Text(f"Item {counter[0]}")),
                ]
            )
        )
        # 方法1: 更新 table
        if table.page:
            table.update()
        # 方法2: 更新 listview
        if listview.page:
            listview.update()
        # 方法3: 更新整个页面
        page.update()
        print(f"Added row {counter[0]}, total rows: {len(table.rows)}")

    def refresh_rows(e):
        # 模拟 _refresh_material_table 的方式
        new_rows = []
        for i in range(1, counter[0] + 1):
            new_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(i))),
                        ft.DataCell(ft.Text(f"Refreshed Item {i}")),
                    ]
                )
            )
        table.rows = new_rows
        if listview.page:
            listview.update()
        page.update()
        print(f"Refreshed, total rows: {len(table.rows)}")

    page.add(
        ft.Row([
            ft.ElevatedButton("Add Row", on_click=add_row),
            ft.ElevatedButton("Refresh Rows", on_click=refresh_rows),
        ]),
        ft.Container(
            content=listview,
            expand=True,
            border=ft.border.all(1, "blue"),
        ),
    )

ft.app(target=main)
