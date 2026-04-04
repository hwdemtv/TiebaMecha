# TiebaMecha 桌面端 (Windows) 打包与分发指南

本文档介绍如何将 TiebaMecha 打包成独立的 Windows `.exe` 文件，以便分发给不具备 Python 开发环境的用户使用。

---

## 1. 准备工作

### 安装打包依赖
本项目使用 `flet pack` (基于 PyInstaller) 进行打包，请确保已安装以下库：

```powershell
pip install flet pyinstaller
```

### 准备图标
- 请确保 `src/tieba_mecha/assets/icon.png` (或指定路径) 存在，建议尺寸 256x256。

---

## 2. 执行打包

在项目根目录下，使用以下命令启动打包流程：

```powershell
flet pack start_web.py `
    --name "TiebaMecha" `
    --icon "src/tieba_mecha/assets/icon.png" `
    --add-data "src;src" `
    --product-name "TiebaMecha 贴吧机甲" `
    --product-version "0.1.0" `
    --copyright "Copyright © 2026 Cyber-Mecha" `
    --company-name "Cyber-Mecha" `
    --description "赛博机甲风格的百度贴吧自动化管理工具"
```

### 参数说明
- `--name`: 生成的可执行文件名。
- `--add-data "src;src"`: **关键参数**，确保所有源代码和资源被包含在包内。
- `--icon`: 程序图标。

---

## 3. 生成结果

打包完成后，您可以在 **`dist/`** 目录下找到生成的 `TiebaMecha.exe`。

> [!TIP]
> **单文件 vs 文件夹**：默认情况下 `flet pack` 会生成较慢的单文件版。
> 如果希望启动速度更快，您可以增加 `--onedir` 参数，这样会生成一个包含所有依赖的文件夹。

---

## 4. 用户分发注意事项

1.  **SQLite 数据库**：`.exe` 运行时会自动在同级目录生成 `data/tieba_mecha.db`。如果您希望预设一些配置，可以手动将 `data/` 文件夹一并分发。
2.  **杀毒软件误报**：由于 PyInstaller 的机制，部分杀毒软件可能会对脚本生成的 `.exe` 产生误报（尤其是没有代码签名的情况下）。请告知用户将其加入白名单或以管理员权限运行。
3.  **硬件指纹变化**：用户在不同机器上运行生成的 `.exe` 时，HWID 会发生变化，需要购买对应的授权卡密进行激活。
