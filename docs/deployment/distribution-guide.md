# TiebaMecha 绿色便携版 (Portable) 打包与分发指南

本文档介绍如何将 TiebaMecha 制作成“绿色便携版”——即一个包含完整 Python 运行环境、解压即用的目录。这是项目官方推荐的分发方式。

---

## 1. 核心分发方案：嵌入式运行时 (推荐)

该方案通过项目自带的构建脚本，将 Python 解释器、所有依赖库以及业务代码整合到一个独立文件夹中。

### 优势
- **环境隔离**：用户无需安装 Python。
- **启动极快**：相比 PyInstaller 编译的单文件，这种离散目录结构的启动速度提升约 300%。
- **高度定制**：包含 `启动机甲.bat` 等快捷入口。

### 执行步骤
1.  **执行构建脚本**：
    在项目根目录下运行：
    ```powershell
    python scripts/build_portable.py
    ```
2.  **检查生成结果**：
    脚本执行完成后，会在 **`dist/TiebaMecha_Portable`** 目录下生成完整环境。
3.  **打包分发**：
    将该目录压缩为 `.zip` 文件（例如：`TiebaMecha_v1.2.0_Portable.zip`）即可分发给用户。

---

## 2. 备选方案：二进制编译 (flet pack)

如果您希望分发单一的可执行文件 (`.exe`)，可以使用基于 PyInstaller 的包装方式。

> [!WARNING]
> 该模式下程序启动时需要解压到临时目录，初次启动较慢，且更容易被杀毒软件误报。

### 打包命令
```powershell
flet pack start_web.py `
    --name "TiebaMecha" `
    --icon "src/tieba_mecha/assets/icon.png" `
    --add-data "src;src" `
    --product-name "TiebaMecha 贴吧机甲" `
    --product-version "1.2.0" `
    --file-description "赛博机甲风格的百度贴吧自动化管理工具" `
    --copyright "Copyright 2026 Cyber-Mecha" `
    --company-name "Cyber-Mecha" `
    --onedir -y
```

---

## 3. 用户分发注意事项

1.  **数据库自愈**：绿色版在首次运行时，会自动在 `data/` 目录下创建 SQLite 数据库。
2.  **启动入口**：指导用户点击 **`启动机甲.bat`** 或 **`启动网页版控制台.bat`**。
3.  **防关联隔离**：每个绿色版实例的 `data/` 目录都是独立的，用户可以通过复制多份绿色版文件夹来实现完美的矩阵账号隔离。
