"""Test Flet API compatibility"""
import flet as ft

print("=== Flet Version ===")
print(ft.__version__ if hasattr(ft, '__version__') else "unknown")

print("\n=== Testing NavigationRail ===")
try:
    nr = ft.NavigationRail(
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.icons.HOME,
                label="Test",
            )
        ]
    )
    print("NavigationRail: OK")
except Exception as e:
    print(f"NavigationRail Error: {e}")

print("\n=== Available Icons (sample) ===")
icons_sample = [x for x in dir(ft.icons) if not x.startswith('_')][:20]
print(icons_sample)

print("\n=== Testing border ===")
try:
    b = ft.border.all(1, "primary")
    print(f"border.all: OK - {b}")
except Exception as e:
    print(f"border.all Error: {e}")

print("\n=== Testing border_radius ===")
try:
    br = ft.border_radius.all(12)
    print(f"border_radius.all: OK - {br}")
except Exception as e:
    print(f"border_radius.all Error: {e}")

print("\n=== Testing colors ===")
try:
    c = ft.colors.with_opacity(0.5, "primary")
    print(f"colors.with_opacity: OK - {c}")
except Exception as e:
    print(f"colors.with_opacity Error: {e}")

print("\n=== Testing BoxShadow ===")
try:
    s = ft.BoxShadow(
        spread_radius=1,
        blur_radius=10,
        color="primary"
    )
    print(f"BoxShadow: OK")
except Exception as e:
    print(f"BoxShadow Error: {e}")

print("\n=== Testing LinearGradient ===")
try:
    g = ft.LinearGradient(colors=["#00B894", "#00C2FF"])
    print(f"LinearGradient: OK")
except Exception as e:
    print(f"LinearGradient Error: {e}")

print("\n=== All tests completed ===")
