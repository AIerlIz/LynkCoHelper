# 领克桌面助手（试用版）

双击启动程序，操作界面在浏览器中打开。电脑负责手机配对与登录态提取，用户确认绑定后，云端负责每日任务。原有命令行脚本保持独立。

## 使用

1. 从 [GitHub Releases](https://github.com/shovelshit/LynkCoHelper/releases) 下载对应平台的 `LynkCoHelper-cloud-*` 启动器。Windows 直接打开 `.exe`；macOS 解压 `*-launcher.zip` 后打开其中的可执行文件。启动器自动下载对应版本的资源包、校验、解压并运行客户端，无需用户安装 Python 或自行打包。
2. 把管理员发来的领取链接粘贴到助手，领取用户身份并保存恢复码。更换电脑时使用恢复码，恢复后旧管理凭证失效。
3. 电脑、手机连接同一局域网。在「绑定账号」选择手机系统和电脑网络。
4. 手机扫码配对，下载名为 `LynkCoHelper-CA.cer` 的本机证书并安装。iPhone 安装描述文件后，还要在「关于本机 → 证书信任设置」开启完全信任。
5. 按页面显示设置手机 Wi-Fi 手动代理，打开领克 App。
6. 获取完整登录态后，助手会静默验证个人信息；验证成功后确认云端绑定。
7. 关闭手机 Wi-Fi 代理，移除本次证书，再在助手中确认断开连接。电脑此后可以关机。

关闭浏览器不会退出后台；使用「退出助手」停止程序。页面刷新丢失连接时，重新双击应用即可。不要在手机代理开启时强制退出。启动时先显示本机已保存的状态，再后台刷新云端；短暂网络故障不会清空当前页面，恢复网络后可点击「刷新」重试。

启动器显示下载进度状态并等待客户端退出。macOS 下载包中是可双击的 `.app`，Windows 是窗口模式 `.exe`；从 SSH 或无图形环境直接运行时才回退到终端输出。使用「退出助手」会清理本次下载与解压目录；关闭启动器也会通知客户端退出，关闭前应先关闭手机代理。强制结束或断电时可能留下临时资源，下次启动仅清理已退出进程的旧目录。账号管理凭据、本机 CA 和设置保留，不属于临时程序资源。每次启动重新下载，需要能访问 GitHub 和其 Release 附件域名。

资源包和启动器放在同一 GitHub Release，资源名为 `LynkCoHelper-cloud-<平台>-resources.tar.gz`，平台为 `windows-x64`、`macos-arm64`、`macos-intel`。不要单独运行资源包；启动器内置对应版本资源的 SHA-256 和固定版本 URL。Release 的 `.sha256` 供人工检查，启动器不从网络获取信任基准。未签名启动器本身仍可能被替换，此方案不等同于发布者数字签名。macOS 首次运行可能受系统安全策略限制。

启动器把资源下载到系统临时目录下的独立版本目录，启动前校验 SHA-256，退出后删除当前目录；异常退出留下的目录会在下次启动时清理。已验证过的资源在同一进程内复用，不会因为打开页面或页面刷新而再次下载。下载会共享一个总时限（最多三次尝试），代理失败后只把剩余时间交给直连，避免 Windows 网络异常时界面长时间显示“未响应”。

分享默认关闭；需要本次手机的 glDevId，IMEI 非必需。登录或 refresh 请求捕获齐全后无需额外抓取 IMEI。不同安卓 App/系统版本是否接受用户证书仍需真机验证。

## 数据

管理凭证仅保存在 macOS 钥匙串或 Windows 凭据管理器，不回退到明文文件。恢复码只在创建或恢复账号时展示，用户可主动下载恢复文件。

手机 token 不显示在界面、不写抓包日志。云端使用 AES-GCM 加密。每台电脑独立生成 CA，手机只下载公开证书，私钥不打包、不上传。解除绑定删除在线登录态与运行记录；云平台历史备份依其保留政策到期删除。

## 开发与打包

普通用户无需 Python/Node；以下命令供开发者使用，Python 3.12、Node.js 22。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r desktop/requirements.lock
.venv/bin/python -m unittest discover -s desktop/tests -v
.venv/bin/python -m desktop.launcher
.venv/bin/python desktop/packaging/build.py
.venv/bin/python desktop/packaging/build_release.py --tag cloud-v0.1.0 --platform macos-arm64
.venv/bin/python desktop/tests/packaged_smoke.py dist/LynkCoHelper.app/Contents/MacOS/LynkCoHelper
```

Windows 使用 `.venv\Scripts\python.exe`。推送到 `main` 的客户端代码或构建流程改动会自动运行测试和 Windows x64、macOS ARM64 / Intel 打包检查。

正式发布时，手动触发 `Build desktop assistant` 并填写新版本号，例如 `v0.1.9` 或 `cloud-v0.1.9`（统一发布为 `cloud-v0.1.9`）；也可以推送新的 `cloud-v*` 标签触发发布。三个平台全部构建成功后发布 GitHub Release，包含启动器、资源包和校验文件；Actions artifact 只是发布过程的中间产物。已有版本禁止覆盖，更新需发布新版本启动器。日常任务不依赖 GitHub Actions。当前发行包没有付费开发者签名/公证，首次分发可能遇到系统安全确认，不应关闭系统安全功能。

`desktop/service.json` 仅包含公开服务地址。云端 Worker、管理后台及数据库结构独立维护在私有仓库 `shovelshit/LynkCoHelper-Cloud`，客户端构建不需要访问该仓库。发布包在启动本地服务或代理前校验网页、服务地址和代理插件的 SHA-256 清单，清单摘要编译进程序，发现缺失或被修改时拒绝启动。源码运行不执行发布校验，发布版没有环境变量跳过入口。此机制不保护可执行文件本身，也不能阻止修改程序以绕过检查，不等同于发布者数字签名。`desktop/tests/ui_harness.py` 只使用测试账号和积分，绝不打包进应用；浏览器测试通过不代表真实账号签到成功。
Windows 启动器在创建窗口前启用 Per-Monitor DPI awareness，并在打包文件中附带 DPI manifest，避免系统位图缩放造成文字模糊。无图形环境才回退到终端进度输出；正常双击会显示下载和校验进度窗口。网页中的本机状态校验使用临时 Bearer token，云端只保存哈希和加密登录态；本地调试与发布包的完整性校验边界不同，校验失败应重新下载可信 Release。

## 验收边界

已进行桌面单元/HTTP 集成测试、真实代理及证书测试、云端真实 D1 集成测试、桌面与 390px 浏览器布局测试、当前 macOS ARM64 打包测试。

概览内可设置两小时执行区间，并从 Bark、Server 酱中选择一个结果推送渠道。每区间默认 10 个名额，显示剩余数量，满额不可新选；暂停保留名额，解绑释放。后端并发校验防止超配。密钥加密保存在云端，不回显；运行记录显示推送状态。账号名称沿用领克个人信息，不提供名称输入框。

“签到时同时完成分享”也适用于立即执行。领克当天已签到时跳过签到写入，仍可继续待完成的分享；助手当天任务已结束则不重复执行。恢复码用于恢复助手管理权限，与领克登录态续期无关。

尚需 Windows/Intel 实机、免费 CPU/子请求测量及 7 天运行观察。推送联调需要用户自己的 Device Key 或 SendKey。通过这些验收前，不宣称所有用户均可零成本即用。
