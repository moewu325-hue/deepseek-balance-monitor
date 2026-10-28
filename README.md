# DeepSeek 余额监控 · 原生精简版

[![Release](https://img.shields.io/github/v/release/moewu325-hue/deepseek-balance-monitor)](https://github.com/moewu325-hue/deepseek-balance-monitor/releases/latest)
![Windows x64](https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-0078D4)
![C++17 / Win32](https://img.shields.io/badge/C%2B%2B17-Win32-00599C)
[![MIT](https://img.shields.io/github/license/moewu325-hue/deepseek-balance-monitor)](LICENSE)

把 DeepSeek API 余额放在桌面上：完整面板、鲸鱼气泡和任务栏余额条三种视图，随时查看余额与刷新状态。适合经常使用 DeepSeek API、希望快速查看账户余量的 Windows 用户。

原生版以 **C++17 + Win32/GDI + WinHTTP** 实现，单个 EXE 直接运行，无需安装 Python 或其他运行时。v2.2.0 将气泡直径放大 50%，加入月光、远山、渐变海水与波纹；DeepSeek 原图标左右游动，跃出时沿抛物线转动，三种模式均显示峰谷红绿灯。

**v2.2.0 实测 512,000 字节（500 KiB）**，相比原打包版约缩小 98.7%。下图录自原生离线窗口，余额为合成数据：

![原生鲸鱼游动与跃出水面，绿灯表示非高峰](docs/whale-swim.gif)

**[下载 EXE](https://github.com/moewu325-hue/deepseek-balance-monitor/releases/latest/download/DeepSeekBalanceMonitor-x64.exe) · [发行与校验和](https://github.com/moewu325-hue/deepseek-balance-monitor/releases/latest) · [English](README_en.md) · [反馈问题](https://github.com/moewu325-hue/deepseek-balance-monitor/issues)**

## 三种显示方式

| 视图 | 显示与交互 | 适用场景 |
| --- | --- | --- |
| 完整面板 | 总余额、赠送余额、充值余额、今日估算减少量与刷新状态 | 查看余额明细 |
| 鲸鱼气泡 | 默认 108 逻辑像素直径，可选五档大小；夜色海湾背景，DeepSeek 图标游动、沿弧线跃出与入水；双击展开、拖动移动 | 桌面常驻，图标与数字更清楚 |
| 任务栏余额条 | 紧凑横条；发现 CodexUsageBar 时停靠在其上方第二层 | 与其他桌面状态条一起使用 |

- **三种模式都有峰谷灯**：红灯为“高峰”，绿灯为“非高峰”，同时显示文字，不只依赖颜色辨识。
- 每 **60 秒**后台刷新，也可右键立即刷新；失败时保留上次余额并标明刷新状态。
- 动画只在气泡内部播放，不会自行移动用户窗口；金额与峰谷灯保留独立位置。
- 右键「气泡大小」可选小、标准、大、超大、特大五档，立即缩放并记住选择；默认直径 108 逻辑像素，系统显示缩放会同步生效。
- 金额采用百万分之一单位的定点整数，界面保留两位小数；多币种分别记录，不混加。
- 与旧 Python 版共用单实例互斥名称，避免两份程序同时写入历史。

## 三步开始

1. 下载 `DeepSeekBalanceMonitor-x64.exe`，放在希望长期保存的位置；系统要求 Windows 10 / 11 x64。
2. 双击运行，从右键菜单选择「输入 API Key」；也可使用下面的已有凭据来源。
3. 右键选择面板、气泡或任务栏模式；需要开机启动时自行启用。

发行页提供 `SHA256SUMS.txt`，可用 PowerShell 核对本地文件：

```powershell
Get-FileHash .\DeepSeekBalanceMonitor-x64.exe -Algorithm SHA256
```

## 密钥与本地数据

凭据依次读取 `DEEPSEEK_API_KEY`、Windows 凭据管理器专用项 `DeepSeekBalanceMonitor.ApiKey`。均不可用时可手动输入；输入框掩码，默认只用于本次运行，勾选保存后也要通过服务端认证才会持久化到专用凭据项。

| 数据 | 位置与处理 |
| --- | --- |
| 界面配置 | `%LOCALAPPDATA%\DeepSeekBalanceMonitor\config.json`，包含气泡大小 `bubble_size` |
| 按币种历史 | 同目录 `usage_log.json`，兼容版本 2 历史格式 |
| 开机启动 | Windows 启动目录的 `DeepSeekBalanceMonitor.vbs`，仅在用户切换菜单时修改 |
| API Key | 环境变量、专用凭据项或当次输入；不写入配置、历史或日志 |

支持旧版 `display_mode`、`collapsed` 配置；损坏文件保留原件，不自动覆盖。程序仅访问固定 HTTPS 余额端点，使用系统证书校验并禁止重定向，不扫描其他应用配置。安全反馈方式见 [SECURITY.md](SECURITY.md)。

## 峰谷与数字如何理解

- 峰谷灯沿用既有规则：**北京时间工作日 09:00–12:00、14:00–18:00 为高峰**（起点包含、终点不含），其余时间与周末为非高峰；不代表 DeepSeek 当前模型报价、实时优惠或实际扣费倍率。
- 今日估算减少量根据余额变化计算，充值、赠送和跨日会影响它，不是官方消费账单。
- 刷新失败时的余额是上次成功结果，应结合刷新状态判断是否为最新值。

## 从源码构建

已验证工具链为 Windows x64、[Zig 0.17.0](https://ziglang.org/download/) 和 PowerShell；运行测试另需 Python 3.10+，仅使用标准库。编译器及 JSON 头文件的锁定版本与哈希见[构建依赖](native/DEPENDENCIES.md)。

```powershell
git clone https://github.com/moewu325-hue/deepseek-balance-monitor.git
cd deepseek-balance-monitor
.\native\build.ps1 -Test
```

产物为 `build\native\DeepSeekBalanceMonitor.exe`；测试不要求提供真实 API Key。可选检查：

```powershell
# 显式运行真实无效凭据的 401 检查
.\native\build.ps1 -Test -Live
# 验证本机已有凭据，仅输出状态
.\build\native\DeepSeekBalanceMonitor.exe --check
```

另提供 MSVC/CMake 项目 `native/CMakeLists.txt`；本次发行验证的是 Zig 构建，未执行 MSVC 构建。

## 目录与技术结构

```text
native/
  core.cpp          金额、API 响应和旧数据格式
  windows.cpp       HTTPS、凭据、原子保存、自启动
  app.cpp           原生视图、动画与后台刷新
  animation.hpp     游动、抛物线跃出与峰谷灯状态
  icon_sprite.hpp   原图标的透明旋转与镜像缓存
  ocean_scene.hpp   月光海湾、远山、波纹与水下气泡
  main.cpp          参数与单实例
  tests/            核心、Windows 与真实窗口检查
  third_party/      JSON 单头文件及许可证
src/                原 Python 实现，供迁移参考
docs/               设计与验证记录
```

## 验证与常见问题

[原生验证记录](docs/native-verification.md)保留 v2.0.0 的体积、核心/Windows 断言、三模式真实窗口与接口检查证据；[动画更新记录](docs/whale-animation-verification.md)记录当前海景气泡、图标、轨迹、灯色及发布产物检查，也保留历史结果。GitHub Actions 另编译并验证原生版；旧 Python 的 `Legacy Python CI` 保留为手动迁移参考，已有测试差异已注明。

**启动后没有余额？** 检查网络、API Key 和刷新状态；可用 `--check` 区分凭据缺失、认证失败或余额不可用，勿把密钥贴到公开 issue。

**更换 EXE 会丢设置吗？** 配置与历史保存在用户数据目录，替换程序文件不删除它们；已有有效凭据可继续使用。

**会调用模型吗？** 余额查询只使用 `api.deepseek.com/user/balance`，不发送聊天或补全请求。

## 许可与致谢

项目采用 [MIT](LICENSE)，鲸鱼图标来源与再分发说明见 [NOTICE](NOTICE)。参考 [CodexUsageBar](https://github.com/luodaoyi/codex-useage-win) 的 Windows 系统 API 架构，原生代码独立编写；本项目与 DeepSeek 无隶属关系。
