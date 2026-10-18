# QuotaLab

把 Codex 额度和工作安排，放在同一个地方。

写到一半才发现额度不够，很容易打乱节奏。QuotaLab 会读取你当前账户的额度，帮你提前留出工作时段，在开工前提醒你，做完后也能留下简单的记录。

[下载 Windows 版 v1.0.0](https://github.com/Zhongzhi18/QuotaLab/releases/download/v1.0.0/QuotaLab-1.0.0-Windows-x64.zip) · [使用指南](docs/使用指南.md) · [更新记录](CHANGELOG.md)

## 平时怎么用

先看一眼额度，再安排下一段工作。

- **概览**：查看 Codex 返回的剩余额度、重置时间，以及接下来的安排。
- **需求规划**：填上日期、开始和结束时间，保存后查看对应建议。
- **开工提醒**：到点在桌面弹出独立窗口。浏览器可以最小化，QuotaLab 后台保持运行即可。
- **使用洞察**：记录实际用了多久、完成了多少，回头看看自己的工作节奏。
- **定时任务准备**：把任务内容和时间整理成说明，再复制到官方应用确认创建。

比如，明天上午想用 Codex 整理一个项目：先安排 09:00–10:00，保存，设好开工前提醒。到时间打开 Codex 开始工作，结束后记下实际用时。日常用这几步就够了。

## 第一次打开

1. 下载上面的 ZIP，完整解压到一个文件夹。
2. 双击 `QuotaLab.exe`，保留旁边的 `_internal` 文件夹。
3. 确保这台电脑上的 Codex 客户端已经安装并登录，等待概览显示额度。
4. 在“需求规划”里创建一段将来的工作时间。

想试试桌面提醒？启用“Windows 桌面开工提醒”，勾选“独立桌面提醒窗口”，发送一条测试通知，再最小化浏览器看看。

更详细的例子见[首次使用](docs/首次使用.md)。

## 几件值得知道的事

QuotaLab 在本机运行，浏览器里的 `127.0.0.1` 就是你的电脑。计划和历史也保存在本机。关闭网页后，后台仍可以提醒；彻底退出请到设置里操作。

额度建议参考的是账户当前返回的读数。定时任务区负责整理说明，已有官方任务仍在官方应用里查看和管理。

目前提供 Windows x64 便携版，程序尚未签名。详细说明见[隐私说明](PRIVACY.md)和[安全说明](SECURITY.md)。QuotaLab 是独立项目。

## 想看看代码？

| 目录 | 内容 |
| --- | --- |
| `app/` | 应用源码、界面和测试 |
| `scripts/` | Windows 构建与安装包检查入口 |
| `docs/` | 使用指南和版本记录 |
| `.github/` | GitHub 自动测试、打包和依赖更新配置；这是平台约定的目录名 |

从源码体验：

```powershell
cd app
python -m pip install tzdata==2025.3
python quota_lab.py start --mock
```

模拟模式可以先体验界面和规划流程。退出后，去掉 `--mock` 即可使用本机已登录的 Codex 账户。

构建 Windows 版使用 Python 3.14.0，在项目根目录运行：

```powershell
python -m pip install -r app/requirements-build.txt
./scripts/build-windows.ps1
```

欢迎在 [Issues](https://github.com/Zhongzhi18/QuotaLab/issues) 提建议、报告问题。附上版本和操作步骤就很有帮助，截图前记得遮住私人内容。

## 许可证

[MIT](LICENSE) · [第三方软件说明](THIRD_PARTY_NOTICES.md)
