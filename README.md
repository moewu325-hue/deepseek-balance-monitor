# DeepSeek 余额监控

一个注重凭据边界的 Windows 桌面余额气泡，只读取 DeepSeek API 账户余额，并以首选币种显示。

## 项目介绍

本项目通过固定的 DeepSeek HTTPS 余额接口定时刷新余额，并在本地估算当天余额减少量。它把正常凭据发现限制为明确的环境变量和专用 Windows 凭据项，避免搜索无关程序、浏览器或任意配置目录。

## 技术栈

- Python 3.10+
- Tkinter 桌面界面
- Requests HTTPS 客户端
- Windows Credential Manager
- Pillow（图标资源验证）

## 快速开始

1. 下载或检出源码，并在项目目录打开终端。
2. 创建环境并安装：`py -3.11 -m venv .venv`，随后运行 `.venv\Scripts\python -m pip install --require-hashes -r requirements.lock` 和 `.venv\Scripts\python -m pip install --no-build-isolation --no-deps -e .`
3. 启动：双击 `run_monitor.bat`，或运行 `.venv\Scripts\deepseek-balance-monitor.exe`

## 目录结构

```text
deepseek-balance-monitor/
├─ assets/
│  └─ deepseek_icon.ico
├─ src/
│  └─ deepseek_balance_monitor/
├─ tests/
├─ config.example.json
├─ requirements.lock
└─ run_monitor.bat
```

## 主要功能

- 在后台线程发现并验证凭据、请求余额，Tk 更新仅在主线程执行。
- 只请求固定端点 `https://api.deepseek.com/user/balance`，启用 TLS 校验、禁止重定向并设置连接/读取超时。
- 401 时排除失效候选并尝试下一个来源；临时网络或服务异常不会永久拒绝凭据。
- 复刻本地版 255×215 余额面板与 100×100 鲸鱼气泡；支持点击展开、拖动定位和左右游动动画。
- 高峰标签固定按北京时间每日 9:00～12:00 和 14:00～18:00 显示，适用于中国及其他地区的用户；它只提示官网计费时段，不重算余额，也不把估算消耗乘以 2。
- 右键菜单可由用户显式开启或关闭开机自启动；默认关闭，变更期间菜单会暂时禁用。
- 缩小按钮和 `✕` 均位于右上角；标题栏关闭、菜单退出和 `✕` 都使用同一二次确认。
- 记录按币种隔离的每日基线和历史，不把不同币种相加。
- 使用 Windows 命名互斥量防止新版程序重复启动。

## 配置说明

运行数据写入 `%LOCALAPPDATA%\DeepSeekBalanceMonitor`，包括不含凭据的 `config.json` 和按币种记录的 `usage_log.json`。历史最多保留最近 10,000 条，相邻且币种与余额都相同的采样不会重复写入。`config.example.json` 展示全部 9 个允许字段；`auto_start` 默认为 `false`。用户开启后，程序只管理 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DeepSeekBalanceMonitor.vbs` 这一专用启动项，关闭后只删除该文件。

币种选择规则：优先使用 `currency` 指定的币种；响应只有一种币种时可回退；响应含多种币种且缺少首选币种时明确报错，绝不相加。CNY 显示为 `¥`、USD 显示为 `$`，其他代码显示为 `CODE 12.34`。

### 凭据来源与人工输入

正常自动识别严格按以下顺序进行：

1. `DEEPSEEK_API_KEY`
2. Windows Credential Manager 的 `DeepSeekBalanceMonitor.ApiKey`

未找到时，界面提供掩码输入框。默认仅在当前进程内存中使用；只有勾选保存且服务端确认凭据已认证后，才写入上述专用 Windows 凭据项。输入值仅用于访问固定的 DeepSeek HTTPS 余额端点进行验证。应用不会把值写入配置、历史或日志；环境变量和 Windows 凭据项是否持久化由相应外部机制负责。

Claude 兼容来源不会参与正常识别。只有显式运行 `deepseek-balance-monitor --migrate-claude-compat` 才会读取与严格官方地址 `https://api.deepseek.com/anthropic`（或显式 `:443`）配对的兼容 Token，以及已知凭据项；程序从不执行 `apiKeyHelper`。

### 命令行

```powershell
deepseek-balance-monitor --check
deepseek-balance-monitor --set-key
deepseek-balance-monitor --migrate-legacy D:\path\to\legacy-directory
deepseek-balance-monitor --migrate-claude-compat
```

`--set-key` 使用隐藏输入且默认不保存。旧版迁移只复制上述非敏感配置字段和余额历史、强制 `auto_start=false`，不会复制旧配置中的 API Key 或未知字段。若旧版曾明文保存凭据，建议迁移后在 DeepSeek 控制台轮换它。

## 安全

安全模型、剩余风险和报告方式见 [SECURITY.md](SECURITY.md)。提交公开仓库前请运行完整测试，并确保没有加入本机配置、历史或凭据。

## License

MIT，详见 [LICENSE](LICENSE)。鲸鱼图标是按用户明确选择随项目提供的旧版资源，其原作者、许可与再分发授权未经独立验证；详见 [NOTICE](NOTICE)。DeepSeek 是其权利人的商标；本项目与 DeepSeek 无隶属、赞助或背书关系。
