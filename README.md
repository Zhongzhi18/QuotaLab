# QuotaLab

一个在本机查看 Codex 额度、安排工作时段并提醒开工的独立工具。

**Windows x64 预览版 · v0.14.3 r31 · 非 OpenAI 官方产品**

## 它能帮你做什么

- 查看当前已登录 Codex 客户端实际返回的额度与重置读数。
- 保存工作计划，查看基于当前读数的建议；不把剩余百分比换算为保证可工作的时长。
- 设置开工提醒。启用后可显示独立 Windows 桌面窗口，不依赖浏览器是否最小化；后台必须运行。
- 记录工作结果，回顾自己的计划和工作负荷。
- 整理官方定时任务说明，供你复制到官方应用核对创建；**不会自动创建或同步官方任务**。

## 下载和首次使用

发布后请从本仓库的 **Releases** 下载具名的 `QuotaLab-0.14.3-Windows-Portable-r31.zip` 及其 SHA256 文件，不要把 GitHub 自动生成的 Source code ZIP 当作可运行应用。

1. 完整解压，双击 `QuotaLab.exe`，保留 `_internal` 文件夹。
2. 确保 Codex 客户端已安装并登录，在概览检查额度读取时间。
3. 新建一个将来的工作时段，保存后确认计划名与右侧建议一致。
4. 需要桌面提醒时，主动启用桌面通道并勾选独立窗口，先发送测试通知。

详见[使用说明](development/v0143/docs/USER_GUIDE_ZH.md)和[首次验收例子](development/v0143/docs/FIRST_RUN_WALKTHROUGH_ZH.md)。

目前为未签名预览程序，不是安装器，未完成所有 Windows 环境兼容性验证。不要为运行程序关闭系统安全保护。

## 隐私与边界

程序监听本机回环地址 `127.0.0.1:18861`，不是公网服务。计划、历史和通知偏好默认保存在本机；不应将运行数据或日志上传到 Issue。

本工具不会增加、购买或保证恢复额度，也不会因保存计划而执行 AI 任务。公开服务状态读取为主动启用的联网功能。

详见 [PRIVACY.md](PRIVACY.md) 和 [SECURITY.md](SECURITY.md)。

## 开发与构建

源码位于 `development/v0143`；根目录不混放旧版应用。普通运行不依赖第三方 Python 包；Windows 构建工具链见 `requirements-build.txt`。

```powershell
cd development/v0143
python quota_lab.py start --mock --no-browser
# 另开浏览器访问本机地址进行模拟体验；不读取真实额度。
```

构建使用 CPython 3.14.0，在 Windows 上执行：

```powershell
python -m pip install -r requirements-build.txt
./scripts/build_windows_v0143.ps1 -BuildTag plan-aware-v0143-r31
```

GitHub Actions 的 Windows preview release 工作流包含集中回归、隔离浏览器验收、构建和解压后的模拟启动检查。首次上传后需在 Actions 手动运行并确认结果；本机通过不等于远端 CI 通过。推送 `v0.14.3-r31` 标签才会在检查通过后创建 **草稿预发布**，不会自动公开发布。

本源码目录经过文件白名单筛选，未包含历史 Git 记录、本机账号资料、运行日志、历史构建包或真实观测衍生测试夹具。发布说明见 [r31 修复说明](development/v0143/docs/RELEASE_NOTES_v0143_r31.md)。

## 许可证

[MIT](LICENSE)。贡献者提交问题时请使用最小复现并删除私人内容。
