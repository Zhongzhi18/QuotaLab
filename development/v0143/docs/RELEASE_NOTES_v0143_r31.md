# QuotaLab r31 · 展开状态与独立桌面提醒

## 修复与原因

1. 原有展开状态机制按页面控件顺序定位；新任务模块在 restoreInteraction 后创建且没有稳定标识，状态恢复不到。r31 给官方任务高级项增加固定键，在重建前捕获、增强控件创建后再次恢复，并忽略已移除节点的延迟 toggle 事件。UI/UX Pro Max 用于交互状态与明确反馈核查，未重做视觉主题。
2. r29/r30 的 Shell 通知提交成功不代表 Windows 实际呈现；无法从用户截图确定是未启用、系统抑制还是后台投递问题。r31 保留原通道，新增独立原生 Windows 提醒窗口，启用桌面提醒后默认勾选。它由后台线程创建，与浏览器窗口无关，持续显示直到主动关闭；不抢键盘焦点，不运行 AI，不修改系统通知/勿扰或开机启动设置。
3. 页面记录最近投递时间、失败/系统提交/独立窗口状态。仍不把窗口创建或系统提交当作用户已看到。关闭窗口不等于取消计划或网页提醒。原有提醒的投递去重保留，不重新播放旧通知。

## 实现依据

- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createwindowexw
- https://learn.microsoft.com/zh-cn/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw

## 验证范围

新增浏览器测试跨真实轮询周期、手动刷新、语言与模式切换验证展开/收起；单元测试覆盖桌面窗口偏好、失败、隐私默认值和后台去重。

本轮 167 项集中回归与 23 项 Chromium 测试通过（含跨页面返回后展开与字段保持）。干净源码发布副本中也通过相同测试、Windows 构建与解压后的模拟启动检查。不同构建的 ZIP 校验值可能不同，请使用与 Release 资产同次生成的 SHA256 文件核验；这里不把原开发目录构建的哈希套到新的 CI 构建上。GitHub 托管 CI 尚未运行。

显式运行 scripts/check_desktop_window_r31.py --native-window，使用隔离测试数据，不启动浏览器。后台到期提醒创建了可见原生窗口，窗口矩形有效，前台窗口未变化，测试后窗口和线程已清理。它证明本机原生窗口通道可用，不证明用户机器休眠/锁屏时也能显示，仍需用户手动复测。

旧包保留。未创建或同步任何真实官方任务；GitHub 托管 CI、干净机器与用户实际桌面验收尚未完成。
