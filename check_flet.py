import flet as ft
import inspect

try:
    import flet.version
    print(f"Flet version: {flet.version.version}")
except (ImportError, AttributeError):
    print("Flet version: unknown")

sig = inspect.signature(ft.ColorScheme.__init__)
for p in sig.parameters.values():
    print(p.name)
