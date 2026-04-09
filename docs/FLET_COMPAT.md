# Flet 版本兼容性指南

## 问题背景

Flet 在不同版本之间会有 API 变更，常见问题包括：

1. **参数名变更**：如 `inverse_on_surface` 在新版本中被移除
2. **参数新增/移除**：如 `surface_container_highest` 在较新版本中添加
3. **类签名变化**：构造函数参数列表变化

## 解决方案

### 1. 使用 `flet_compat.py` 工具模块

```python
from flet_compat import safe_color_scheme, safe_theme, get_flet_version

# 安全创建 ColorScheme（自动过滤不支持的参数）
color_scheme = safe_color_scheme(
    primary="#00D4FF",
    secondary="#B4CDCD",
    inverse_on_surface="#2A302F",  # 可能不支持，自动处理
)

# 安全创建 Theme
theme = safe_theme(color_scheme=color_scheme)
```

### 2. 查询支持的参数

```python
from flet_compat import get_supported_color_scheme_params

supported = get_supported_color_scheme_params()
print(f"支持的参数: {supported}")
```

### 3. 使用构建器模式

```python
from flet_compat import ColorSchemeBuilder

color_scheme = (ColorSchemeBuilder()
    .primary("#00D4FF")
    .secondary("#B4CDCD")
    .surface("#121820")
    .build())
```

## 最佳实践

### ✅ 推荐做法

```python
# 1. 使用 safe_color_scheme 自动处理兼容性
def get_dark_theme() -> ft.Theme:
    color_scheme = safe_color_scheme(
        primary="#00D4FF",
        secondary="#B4CDCD",
        # 所有参数直接传入，不支持的会被自动过滤
        inverse_surface="#E1E3E1",
        inverse_on_surface="#2A302F",
    )
    return safe_theme(color_scheme=color_scheme)
```

### ❌ 避免做法

```python
# 直接使用 ft.ColorScheme，可能在新版本中报错
def get_dark_theme() -> ft.Theme:
    return ft.Theme(
        color_scheme=ft.ColorScheme(
            primary="#00D4FF",
            inverse_on_surface="#2A302F",  # 可能导致 TypeError
        )
    )
```

## 常见兼容性问题

| 参数 | 状态 | 说明 |
|------|------|------|
| `inverse_surface` | ⚠️ | 某些版本支持 |
| `inverse_on_surface` | ⚠️ | 某些版本支持，已改名为 `on_inverse_surface` |
| `inverse_primary` | ⚠️ | 某些版本支持 |
| `surface_container_highest` | ✅ | Flet 0.83+ 支持 |
| `surface_tint` | ✅ | 较新版本支持 |

## 版本检测

```python
from flet_compat import get_flet_version

version = get_flet_version()
if version >= (0, 83, 0):
    # 使用新版本特性
    pass
```

## 文件位置

- **TiebaMecha**: `src/tieba_mecha/web/flet_compat.py`
- **ZhihuMecha**: `src/zhihu_mecha/web/flet_compat.py`

## 升级 Flet 时的检查清单

1. 运行测试确认 `flet_compat.py` 正常工作
2. 检查 `get_supported_color_scheme_params()` 返回的参数列表
3. 更新主题配置中的颜色值
4. 测试 Web UI 启动和页面渲染
