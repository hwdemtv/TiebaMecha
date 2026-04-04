# TiebaMecha 授权与系统广播对接技术文档

本文档旨在详述 TiebaMecha 客户端与远程授权中心 (`hw-license-center`) 的通信协议、身份识别机制及全向广播同步逻辑。

---

## 1. 硬件指纹 (HWID) 生成算法

系统通过唯一的硬件组合确保授权的单机绑定性。

- **核心因子**：
  - **主板序列号 (SerialNumber)**：获取自 `Win32_BaseBoard`。
  - **CPU ID (ProcessorId)**：获取自 `Win32_Processor`。
- **降级方案**：若 WMI 接口受限，系统将回退至物理 MAC 地址 (`uuid.getnode()`)。
- **脱敏处理**：
  - 组合字符串核心：`{BaseBoardID}-{CPUID}`
  - 生成算法：对组合字符串进行 `SHA256` 摘要，提取前 32 位并转为大写。
  - **示例**：`6BB32658A151E8CA6A46C8A51804C8C3`

## 2. 在线验证协议 (Authentication)

客户端通过异步多节点轮询机制请求授权状态。

### 2.1 请求参数
- **Endpoint**: `/api/v1/auth/verify` (POST)
- **Payload (JSON)**:
  ```json
  {
    "license_key": "YOUR_LICENSE_KEY",
    "device_id": "HWID_GENERATED_BY_CLIENT",
    "product_id": "tieba_mecha", // [强制] 必须为全小写下划线格式
    "mode": "check"               // [必填] 部分边缘节点(Tier 3)透传依据
  }
  ```
> [!IMPORTANT]
> **字段规范提示**：请勿将产品名误写为 PascalCase (TiebaMecha)，否则会导致后端授权系统因 ProductID 不匹配而返回 404 (Not Found)。

### 2.2 响应结构
- **200 OK (Success)**:
  ```json
  {
    "success": true,
    "status": "pro",
    "info": {
      "expires_at": "2026-12-31",
      "features": ["ai_optimizer", "batch_unlimited"]
    },
    "notification": { ... } // 同步携带一个即时广播
  }
  ```

## 3. 系统广播同步逻辑 (Broadcast Sync)

机甲支持 **“无感·全向”** 广播。即使在无授权密钥（Free 用户）的情况下，系统也会自动拉动全域公告。

- **执行频率**：每 1 小时自动同步一次，由后台守护进程（Daemon）驱动。
- **请求模式**：`silent` 模式。
  - 此模式下，服务端仅下发公共广播，不执行严格的设备绑定或扣点逻辑。
  - 客户端允许 `license_key` 为空字符串。
- **入库流程**：
  1. 比较远程通知的 `id` 是否已存在于本地数据库 `notifications` 表。
  2. 若为新消息且标记为 `is_force=True`，系统将立即弹出 Flet SnackBar 强提示。
  3. 同步完成后，点击导航栏铃铛图标即可查看完整历史。

## 4. 容灾机制 (Failover Handling)

系统内置了三层防御式探测路径，确保护持握手的鲁棒性：

1.  **Tier 1 (主控机)**：`km.hwdemtv.com`
2.  **Tier 2 (备机)**：`kami.hwdemtv.com`
3.  **Tier 3 (边缘侧)**：`hw-license-center.hwdemtv.workers.dev`

**探测顺序与优先级**：
`数据库自定义 URL (license_server_url) > Tier 1 > Tier 2 > Tier 3`

> [!WARNING]
> **失效清理机制**：若数据库中存储的自定义域名已失效（如 `license.hubinwei.top`），系统会因其优先级最高而持续报错。此时必须在数据库 `settings` 表中清空该字段，以释放优先级并让系统回退至内置容灾链路。

## 5. 安全性与离线支持

- **心跳机制**：每 6 小时自动重连云端，刷新 JWT 状态。
- **离线宽限**：若网络完全中断，系统将回退至本地离线状态（FREE 或 ERROR 模式），并记录异常，提示用户在网络恢复后手动校验。
- **隐私保护**：系统严格遵循不上传明文隐私的原则，仅上传 SHA256 混淆后的摘要值。

---

## 6. 技术注意事项与踩坑点

### 6.1 Cloudflare 边缘防护 (Error 1010)
由于 `hw-license-center` 部署在 Cloudflare 边缘节点，Python 默认的 `aiohttp` 请求头会被识别为机器人并拦截（HTTP 403）。
- **对策**：在 `auth.py` 和 `notification.py` 中，所有外发请求必须携带浏览器风格的 `User-Agent`。本项目统一使用了：`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36`。

### 6.2 HTTP 403 & 404 的业务语义
后端将特定的业务状态通过 HTTP 状态码映射：
- **404**：卡密不存在/无效码。
- **403**：当前卡密绑定的 HWID 与请求不符（设备超限）。

- **误区**：常规逻辑会认为 4xx 是接口或网络故障。
- **项目处理**：本项目重写了响应拦截。只要状态码在 `[200, 403, 404]` 范围内，系统均会尝试 `resp.json()` 解析。若 `success=false`，则提取 `message` 或 `msg` 字段进行全系统 WARNING 播报，而非触发 ERROR 重试，从而提升诊断透明度。

---

## 7. 环境与 UI 兼容性 (Runtime Compatibility)

### 7.1 Flet 版本适配 (M3 Roles)
由于本地生产环境可能停留在 **Flet 0.23.2**（而非 v3.0/0.83.1+），Theme 主题配置时必须注意以下 API 差异：
- **容器背景**：严禁使用 `surface_container` 或 `surface_container_highest` 等 M3 专属角色，否则会导致 `TypeError`。
- **兼容写法**：统一回退至 `background`、`surface` 或 `surface_variant`。

### 7.2 解释器冲突警告
若系统 Python (3.11) 与当前项目 `.venv` 安装的库版本不一致：
1. 请务必确认 `start_web.py` 调用的解释器路径。
2. 若在生产环境强行升级 Flet，必须同步回归所有页面组件的配色方案。
