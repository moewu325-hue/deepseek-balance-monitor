# 原生精简版验证记录

验证日期：2026-10-21；平台：本机 Windows x64，125% 显示缩放；编译器：经官方 SHA-256 核验的 Zig 0.17.0。

## 最终产物

- 原版 EXE：39,188,947 字节；原生版：499,712 字节（488 KiB），减小约 98.7%。
- SHA-256：`27d0fb46f3ce583a23ab7926232f13215ccd7ca3779b481fadf2046d44899ba4`。
- PE GUI subsystem 2；ASLR/NX 标记开启。导入均为 Windows 系统 API 与内置 Universal CRT，无 Python、Tk、OpenSSL、WebView 或外置 C++ DLL。

## 已运行验证

最终命令：`native/build.ps1 -Zig <已核验 Zig 路径> -Test -Live`，退出码 0。

- 核心 46 个断言通过：定点金额与舍入、非法/超精度金额、多币种选择、重复币种/错误 JSON、旧配置字段与密钥字段隔离、历史往返、按币种跨日基线、重复采样去重、严格时间戳、UTF-8 中文路径转换。
- Windows 22 个断言通过：密钥头部注入与长度限制、中文文件名、文件大小上限、锁定目标替换失败保留原文、写入中强杀后旧文件完整、重启后可再次原子保存、损坏配置拒绝载入、UTF-8 BOM 写入读回、真实测试凭据返回 401。
- 三模式真实窗口与 PrintWindow 通过：125% 缩放下面板 425×298、气泡 90×90、横条 230×58；TOOLWINDOW、不置顶、非空绘制、正常退出、非法参数边界通过。
- 离线 smoke 不读真实凭据、不发网络、不修改启动项；真实 config.json / usage_log.json 前后 SHA-256 不变。另保留临时 LOCALAPPDATA 无产物检查，但不将环境变量覆盖误当作 Windows Known Folder 隔离证明。
- 最终 GUI EXE 的 `--check`：退出码 0，脱敏输出 `credential=verified http=200 balance=available`。
- 普通 GUI 启动与兼容单实例互斥通过；第二实例退出码 0，首实例继续运行；普通实例已确认退出。测试过程未修改密钥或开机启动设置。
- `git diff --check`、两个 GitHub workflow 的 YAML 解析与 vendored JSON 头文件 SHA-256 通过。
- 两轮独立代码审查未发现 Critical / Important 缺陷；时间戳后缀与 BOM 读回问题经失败测试复现后修正。首次可见窗口可能先于有效首帧，测试加入最多 0.5 秒的绘制就绪等待，并按物理 DPI 捕获。

真实接口契约证据：GET `https://api.deepseek.com/user/balance`，Bearer 仅存在请求头内；2026-10-21 实际成功 200、Content-Type application/json、字段 is_available(bool)、balance_infos(list)，列表中 currency、total_balance、granted_balance、topped_up_balance，与 [官方文档](https://api-docs.deepseek.com/api/get-user-balance/) 一致。实际金额与凭据不写入本记录。无效测试凭据实际返回 401，根对象含 error。

## 验证范围

- 发布验证构建为 Zig；附带的 MSVC/CMake 配置未在本机执行。
- 密钥对话框的实际保存及开机启动脚本的登录执行未作完整运行验证；专用凭据编码、200/402 认证后才保存、默认不勾选保存及 VBS UTF-16 / 引号 / 绝对路径经过源码审查。
- 退出时隐藏窗口并等待在途请求返回；未实现即时取消网络请求。仅验证本机显示环境，未覆盖所有多显示器缩放组合。
- TLS 验证与禁止重定向使用 WinHTTP 系统默认和明确安全标志，未通过改写固定生产域名注入自签名证书或重定向服务器。

## 原有源码保留

原有 Python 工作区差异的源文件 diff 哈希在实现前后均为 `c60227e1335011e253c09edc8f2819c7e65d994d`，旧修改完整保留。迁移前 Python 基线为 216 项，存在 3 个失败和 3 个错误：两个旧 allowlist 断言未包含 display_mode、三个 fake UI 与新增显示模式/窗口 API 不匹配、离线 wheel 的 setuptools 版本不匹配。原生 CI 独立验证新版本；旧 Python CI 保留为手动迁移参考。

本轮编译器、核验截图、构建测试二进制与一次性状态文件在交付后清理；Git 中保留源代码、可复跑构建脚本、测试、依赖许可证及本记录。

## 发布交付

公开仓库：https://github.com/moewu325-hue/deepseek-balance-monitor（2026-10-21 按用户要求由私有改为公开） 。Release：https://github.com/moewu325-hue/deepseek-balance-monitor/releases/tag/v2.0.0 ，对应软件源码提交 `afea54a225a5237add946de019a1032bf1d48807`。已通过 GitHub API 重新下载 Release EXE，499,712 字节，SHA-256 与本机发布产物完全一致。

桌面现行入口 `DeepSeekBalanceMonitor-x64.exe` 已替换旧版；旧 EXE、一次性编译器和新建测试构建目录已送入回收站。原始项目其它历史文件未作清理。当前版本的 GitHub Windows 构建记录：https://github.com/moewu325-hue/deepseek-balance-monitor/actions/runs/39585575298 。
