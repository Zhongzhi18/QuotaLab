# QuotaLab v0.14.3 r31 source

这是 Windows x64 预览候选的源代码目录。请先阅读[仓库首页](../../README.md)、[使用说明](docs/USER_GUIDE_ZH.md)和[发布说明](docs/RELEASE_NOTES_v0143_r31.md)。

便携包由 `scripts/build_windows_v0143.ps1` 在 Windows 构建；构建产物不提交到源码 Git 仓库，下载通过 Releases 提供。构建和测试中的本机路径必须保持通用，不包含个人凭据或真实观测数据。

当前提交的测试集为发布工作流所需的集中回归与浏览器验收及其基础依赖，不宣称包括历史所有研究用例。官方任务创建/同步未实现；模拟测试不发送真实任务。
