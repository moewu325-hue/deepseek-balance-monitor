# DeepSeek 余额监控 · 原生精简版

Windows 桌面余额小工具，C++17 + Win32/GDI + WinHTTP。单个 EXE，直接运行，无需安装 Python 或其他运行时。

## 下载与使用

从本仓库 Releases 下载 `DeepSeekBalanceMonitor-x64.exe`。支持 Windows 10 / 11 x64。

- **面板**：总余额、赠送余额、充值余额、今日估算减少量和刷新状态。
- **气泡**：鲸鱼轻动效与余额，双击展开。
- **任务栏**：紧凑余额条；找到 CodexUsageBar 时停靠在其上方第二层。
- 拖动面板或气泡移动；右键切换模式、刷新、输入密钥或管理开机启动。
- 每 60 秒自动刷新；断网时保留上次余额，显示失败状态。

## 凭据与兼容

优先读取 `DEEPSEEK_API_KEY`，随后读取 Windows 凭据管理器中的 `DeepSeekBalanceMonitor.ApiKey`。均不可用时，从右键菜单选择「输入 API Key」；输入框掩码，默认仅用于本次运行，勾选保存后也只有通过服务端认证才会持久化到该专用凭据项。

配置和历史继续使用 `%LOCALAPPDATA%\DeepSeekBalanceMonitor\config.json`、`usage_log.json`。支持旧版 `display_mode`、`collapsed` 及版本 2 的按币种历史。损坏文件不会自动覆盖。开机启动沿用启动目录的 `DeepSeekBalanceMonitor.vbs`，仅在用户切换菜单时修改。

金额使用百万分之一单位的定点整数，界面保留两位小数；多币种分别记录。今日数值是余额减少估算，充值、赠送和跨日会影响它，不是官方消费账单。「高峰时段」只沿用旧版的北京时间工作日时段提示，不代表当前模型报价或实际扣费倍率。

仅访问固定 HTTPS 余额端点，使用系统证书校验，禁止重定向；不扫描其他应用配置，不将密钥写入配置、历史或日志。源代码保留原 Python 实现供迁移参考，原生版本使用相同的单实例互斥名称，防止同时写历史。

## 从源码构建

安装 [Zig 0.17.0](https://ziglang.org/download/) 并加入 PATH，Windows PowerShell 执行：

```powershell
.\native\build.ps1 -Test
# 显式运行真实无效凭据 401 检查：
.\native\build.ps1 -Test -Live
# 验证本机已有凭据，仅输出状态：
.\build\native\DeepSeekBalanceMonitor.exe --check
```

测试脚本需要 Python 3.10+，只使用标准库；EXE 运行不依赖 Python。产物为 `build\native\DeepSeekBalanceMonitor.exe`。另提供 MSVC/CMake 项目 `native/CMakeLists.txt`，本次发布已验证的构建为 Zig Windows x64。

```text
native/
  core.cpp          金额、API 响应与旧数据格式
  windows.cpp       HTTPS、凭据管理器、原子保存、自启动
  app.cpp           三种原生视图与后台刷新
  main.cpp          CLI 与单实例
  tests/            业务、崩溃恢复与真实窗口检查
  third_party/      锁定的 nlohmann/json 单头文件与许可证
```

## 验证与依赖

详见 [原生验证记录](docs/native-verification.md) 和 [构建依赖](native/DEPENDENCIES.md)。GitHub Actions 编译原生版并运行自动检查；旧 Python 的历史测试可通过 `Legacy Python CI` 手动触发，迁移前已存在的测试差异在验证记录中列出。

参考 [CodexUsageBar](https://github.com/luodaoyi/codex-useage-win) 的 Windows 系统 API 架构，原生代码独立编写。MIT 许可见 [LICENSE](LICENSE)；鲸鱼图标的来源与再分发说明见 [NOTICE](NOTICE)。本项目与 DeepSeek 无隶属关系。
