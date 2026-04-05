"""最小复现测试：DataTable 在 ListView 中不更新"""
import flet as ft

def main(page: ft.Page):
    page.title = "DataTable Update Test"

    # 场景1：DataTable 直接放在页面
    table1 = ft.DataTable(
        columns=[ft.DataColumn(ft.Text("ID")), ft.DataColumn(ft.Text("Name"))],
        rows=[],
    )

    # 场景2：DataTable 放在 ListView 中 (类似原始代码)
    table2 = ft.DataTable(
        columns=[ft.DataColumn(ft.Text("ID")), ft.DataColumn(ft.Text("Name"))],
        rows=[],
    )
    listview2 = ft.ListView(
        [ft.Row([table2], scroll=ft.ScrollMode.ADAPTIVE)],
        expand=True,
    )

    # 场景3：DataTable 直接作为 ListView 的子元素
    table3 = ft.DataTable(
        columns=[ft.DataColumn(ft.Text("ID")), ft.DataColumn(ft.Text("Name"))],
        rows=[],
    )
    listview3 = ft.ListView([table3], expand=True)

    counter = [0]

    def add_rows(e):
        counter[0] += 1
        row = ft.DataRow(cells=[
            ft.DataCell(ft.Text(str(counter[0]))),
            ft.DataCell(ft.Text(f"Item {counter[0]}")),
        ])

        # 更新三个表格
        table1.rows.append(row)
        table2.rows = list(table2.rows) + [ft.DataRow(cells=[
            ft.DataCell(ft.Text(str(counter[0]))),
            ft.DataCell(ft.Text(f"Item {counter[0]}")),
        ])]
        table3.rows = list(table3.rows) + [ft.DataRow(cells=[
            ft.DataCell(ft.Text(str(counter[0]))),
            ft.DataCell(ft.Text(f"Item {counter[0]}")),
        ])]

        print(f"table1 rows: {len(table1.rows)}")
        print(f"table2 rows: {len(table2.rows)}")
        print(f"table3 rows: {len(table3.rows)}")

        # 尝试不同的更新方式
        table1.update()
        table2.update()
        table3.update()
        # listview2.update()
        # listview3.update()
        page.update()

    page.add(
        ft.ElevatedButton("Add Row", on_click=add_rows),
        ft.Text("场景1: DataTable 直接在页面"),
        ft.Container(content=table1, border=ft.border.all(1, "red"), height=150),
        ft.Text("场景2: DataTable 在 Row 在 ListView"),
        ft.Container(content=listview2, border=ft.border.all(1, "blue"), height=150),
        ft.Text("场景3: DataTable 直接在 ListView"),
        ft.Container(content=listview3, border=ft.border.all(1, "green"), height=150),
    )

ft.app(target=main)
