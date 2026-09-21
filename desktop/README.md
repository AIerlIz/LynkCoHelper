# 领克桌面助手（试用版）

双击启动程序，操作界面在浏览器中打开。电脑负责手机配对与登录态提取，用户确认绑定后，云端负责每日任务。原有命令行脚本保持独立。

## 使用

1. 解压发行包，打开 `LynkCoHelper.app`（macOS）或 `LynkCoHelper.exe`（Windows）。Windows 请保留同目录依赖文件。
2. 把管理员发来的领取链接粘贴到助手，领取用户身份并保存恢复码。更换电脑时使用恢复码，恢复后旧管理凭证失效。
3. 电脑、手机连接同一局域网。在「绑定账号」选择手机系统和电脑网络。
4. 手机扫码配对，安装本机证书。iPhone 安装描述文件后，还要在「关于本机 → 证书信任设置」开启完全信任。
5. 按页面显示设置手机 Wi-Fi 手动代理，打开领克 App。
6. 获取完整登录态后，勾选上传授权，验证并确认云端绑定。
7. 关闭手机 Wi-Fi 代理，移除本次证书，再在助手中确认断开连接。电脑此后可以关机。

关闭浏览器不会退出后台；使用「退出助手」停止程序。页面刷新丢失连接时，重新双击应用即可。不要在手机代理开启时强制退出。

分享默认关闭；需要本次手机的 glDevId，IMEI 非必需。登录或 refresh 请求捕获齐全后无需额外抓取 IMEI。不同安卓 App/系统版本是否接受用户证书仍需真机验证。

## 数据

管理凭证仅保存在 macOS 钥匙串或 Windows 凭据管理器，不回退到明文文件。恢复码只在创建或恢复账号时展示，用户可主动下载恢复文件。

手机 token 不显示在界面、不写抓包日志。云端使用 AES-GCM 加密。每台电脑独立生成 CA，手机只下载公开证书，私钥不打包、不上传。解除绑定删除在线登录态与运行记录；云平台历史备份依其保留政策到期删除。

## 开发与打包

普通用户无需 Python/Node；以下命令供开发者使用，Python 3.12、Node.js 22。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r desktop/requirements.lock
.venv/bin/python -m unittest discover -s tests/desktop -v
.venv/bin/python -m desktop.launcher
.venv/bin/python desktop/packaging/build.py
.venv/bin/python tests/desktop/packaged_smoke.py dist/LynkCoHelper.app/Contents/MacOS/LynkCoHelper
```

Windows 使用 `.venv\Scripts\python.exe`。手动触发 `Build desktop assistant` 工作流可构建 Windows x64、macOS ARM64 / Intel；日常任务不依赖 GitHub Actions。当前发行包没有付费开发者签名/公证，首次分发可能遇到系统安全确认，不应关闭系统安全功能。

`desktop/service.json` 仅包含公开服务地址。云端 Worker、管理后台及数据库结构独立维护在私有仓库 `shovelshit/LynkCoHelper-Cloud`，客户端构建不需要访问该仓库。发布包在启动本地服务或代理前校验网页、服务地址和代理插件的 SHA-256 清单，清单摘要编译进程序，发现缺失或被修改时拒绝启动。源码运行不执行发布校验，发布版没有环境变量跳过入口。此机制不保护可执行文件本身，也不能阻止修改程序以绕过检查，不等同于发布者数字签名。`tests/desktop/ui_harness.py` 只使用测试账号和积分，绝不打包进应用；浏览器测试通过不代表真实账号签到成功。

## 验收边界

已进行桌面单元/HTTP 集成测试、真实代理及证书测试、云端真实 D1 集成测试、桌面与 390px 浏览器布局测试、当前 macOS ARM64 打包测试。

概览内可设置两小时执行区间，并从 Bark、Server 酱中选择一个结果推送渠道。每区间默认 10 个名额，显示剩余数量，满额不可新选；暂停保留名额，解绑释放。后端并发校验防止超配。密钥加密保存在云端，不回显；运行记录显示推送状态。账号名称沿用领克个人信息，不提供名称输入框。

“签到时同时完成分享”也适用于立即执行。领克当天已签到时跳过签到写入，仍可继续待完成的分享；助手当天任务已结束则不重复执行。恢复码用于恢复助手管理权限，与领克登录态续期无关。

尚需 Windows/Intel 实机、免费 CPU/子请求测量及 7 天运行观察。推送联调需要用户自己的 Device Key 或 SendKey。通过这些验收前，不宣称所有用户均可零成本即用。
