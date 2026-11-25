# earImmersion（磨耳朵）· 划词发音

选中英文 → 出现小喇叭 → 点击听标准发音，旁边显示音标和中文释义。
单词、缩写（PLAID）、多个词、整句都可以，多个词会连起来读。

两种形态，按需选用，可以同时装：

| | 浏览器扩展 `extension/` | 桌面版 `desktop/speak.py` |
|---|---|---|
| 适用 | 网页、arXiv HTML、在线 PDF.js 阅读器 | 任何软件：PDF 阅读器、Zotero、Word、浏览器内置 PDF |
| 体积 | 3 个文件共 10 KB | 源码 1 个文件 53 KB；免安装包约 12 MB（自带 Python 运行环境） |
| 依赖 | 无 | 免安装包：无；从源码运行：只需 Python 3（无第三方库） |
| 平台 | Chrome / Edge / Firefox（Win / Mac / Linux） | Windows 10 / 11（64 位）、macOS、Linux（X11） |

## 浏览器扩展

**Chrome / Edge**：打开 `chrome://extensions` → 打开右上角「开发者模式」→「加载已解压的扩展程序」→ 选 `extension` 文件夹。

**Firefox**：打开 `about:debugging#/runtime/this-firefox` →「临时载入附加组件」→ 选 `extension/manifest.json`（重启 Firefox 后需重新载入）。

装好后**已经打开的页面要刷新一次**才生效。浏览器内置的 PDF 查看器不允许扩展介入，读本地 PDF 请用桌面版。

## 桌面版

到 [Releases](../../releases/latest) 下载，不需要装 Python。

### Windows

下载 `earImmersion-windows-x64.exe`，双击运行，不用安装，放在哪里都行。

**怎样算启动成功**：双击后一两秒，屏幕右下角会弹出一张「earImmersion 已启动」的提示（几秒后自己消失），任务栏右下角多出一个蓝色喇叭图标（可能收在 `^` 里）。

- 用法：在任何软件里拖选或双击选中英文，鼠标旁出现小喇叭，点它。
- 退出：右键托盘里的喇叭图标 →「退出」；或者再双击一次 exe，在弹出的对话框里选「是」。
- 开机自启：`Win + R` 输入 `shell:startup`，把 exe 的快捷方式放进打开的文件夹。

**打不开时**，按顺序看：

1. 浏览器不让下载（Edge 提示「通常不会下载」）：点「…」→「保留」→「仍然保留」。
2. 双击后出现蓝色的「Windows 已保护你的电脑」：点「更多信息」→「仍要运行」。程序没有购买代码签名证书，所以会有这一步，只需一次。
3. 杀毒软件（360、火绒、电脑管家等）拦截或直接删掉了 exe：这类单文件 Python 程序经常被误报，需要在杀毒软件里「信任 / 恢复」。发布的 exe 由 GitHub 的机器从本仓库源码构建，并用 Microsoft Defender 扫描过；不放心可以改用下面的「从源码运行」。
4. 双击后什么都没出现：再双击一次。如果弹出「earImmersion 正在运行」，说明它已经在运行了，直接去选词试试；如果弹出的是报错，请把报错内容发到 [Issues](../../issues)。
5. 仍然不行：请把 `%LOCALAPPDATA%\earImmersion\log.txt`（在资源管理器地址栏粘贴 `%LOCALAPPDATA%\earImmersion` 回车即可看到；程序能启动时也可以右键托盘图标 →「打开日志所在的文件夹」）发到 Issues。日志只记录动作，不记录你选中的文字。

其他情况：

- 需要 64 位的 Windows 10 或 11。Windows 7 / 8 和 32 位系统运行不了这个 exe。
- 点了喇叭有释义但没声音、提示「播放失败」：名字里带 N 的 Windows 版本（如「Windows 10 专业版 N」）缺少媒体组件，需要在「设置 → 应用 → 可选功能」里添加「媒体功能包」。
- 某个软件里选了字却不出喇叭：见下面「配置」里的 `any_cursor`。

### macOS

Apple 芯片下载 `earImmersion-macos-arm64.zip`，Intel 下载 `earImmersion-macos-x64.zip`，解压后把 `earImmersion.app` 拖进「应用程序」。

应用没有 Apple 的签名，第一次要手动放行，并给它「辅助功能」权限（模拟 ⌘C 取词要用）：

1. 双击打开，系统会提示无法验证这个应用 → 点「完成」；然后到「系统设置 → 隐私与安全性」，拉到底部点「仍要打开」。（macOS 14 及更早版本：在应用上点右键 →「打开」。也可以在终端执行 `xattr -cr /Applications/earImmersion.app`。）
2. 启动后屏幕右上角会弹出「earImmersion 已启动」的提示，同时系统会请求「辅助功能」权限：到「系统设置 → 隐私与安全性 → 辅助功能」里打开 earImmersion 的开关。授权后如果还取不到词，退出重开一次。
3. **以后每次更新版本**都要在「辅助功能」列表里把旧的 earImmersion 删掉（选中后点「−」）再重新添加，否则系统会认为权限还在、实际却不生效。

- 退出：在程序坞（Dock）的 earImmersion 图标上点右键 →「退出」。点一下 Dock 图标可以再看一遍启动提示。
- 开机自启：「系统设置 → 通用 → 登录项」里添加 earImmersion。

### 从源码运行

| 系统 | 启动 | 备注 |
|---|---|---|
| Linux | `python3 desktop/speak.py` | 用系统自带的 GTK；在 conda 环境里运行会自动换成 `/usr/bin/python3`。退出：再运行一次 |
| Windows | `pythonw desktop\speak.py` | 需要 python.org 的 Python 3 |
| macOS | `python3 desktop/speak.py` | 需要 python.org 的 Python 3；「辅助功能」权限要给终端 / Python |

Linux 开机自启：新建 `~/.config/autostart/earimmersion.desktop`，内容为

```
[Desktop Entry]
Type=Application
Name=earImmersion
Exec=/usr/bin/python3 /你的路径/earImmersion/desktop/speak.py
```

### 用法细节

拖选或双击选词，鼠标旁出现喇叭；点击播放，再点一次重听。点别处，或 4 秒不理它（点过之后是 20 秒），喇叭自动消失。

- **Linux**：一选中就知道选了什么，非英文（如中文）不会出喇叭；鼠标移到喇叭上就开始预取，点击更快。
- **Windows / macOS**：系统不提供「刚选中了什么」，所以喇叭先出现，**点击时**才模拟一次 Ctrl+C / ⌘C 取词，随后把剪贴板里原有的**文本**放回去（如果剪贴板里原来是图片或文件，会丢失）。不点喇叭就不会碰剪贴板。
- **Windows**：鼠标是普通箭头时的拖动和双击（拖窗口、点图标）不会出喇叭。

## 配置

**桌面版**：在数据目录里新建 `config.json`（改完重启程序），三项都可省略：

```json
{"accent": 2, "max_len": 300, "any_cursor": false}
```

- `accent`：`2` 美音（默认），`1` 英音。
- `max_len`：选中内容超过多少个字符就不读（默认 300）。
- `any_cursor`（仅 Windows）：设为 `true` 后，鼠标是普通箭头时拖动 / 双击也出喇叭。有些软件在文字上不显示「I」形光标，在这些软件里不出喇叭时打开它。

数据目录里还有日志 `log.txt` 和发音缓存 `voice/`（最多 1000 个文件，听过的词再点是瞬时的）：

| 系统 | 数据目录 |
|---|---|
| Windows | `%LOCALAPPDATA%\earImmersion` |
| macOS | `~/Library/Caches/earImmersion` |
| Linux | `~/.cache/earImmersion` |

**浏览器扩展**：改文件顶部的常量——`extension/bg.js` 里的 `ACCENT`，`extension/content.js` 里的 `MAX_LEN`。

## 工作方式

- 发音：优先有道词典真人发音；有道没有收录的短语 / 句子用百度语音合成。
- 释义：有道词典（单词给音标 + 词性释义，短语和句子给翻译；几个不相关的词一起选时逐词给释义）。
- 隐私：只有点击喇叭时（Linux 和浏览器扩展：鼠标移到喇叭上时），才会把**选中的那段文本**发给有道 / 百度；仅仅选中不会联网。程序不收集任何数据。
- 桌面版同一时间只运行一个：再启动一次会询问是否退出正在运行的那个（靠本机端口 47631 互相识别）。

## 测试情况

每次发布前，GitHub Actions 在真实的 Windows 和 macOS 机器上先跑端到端测试（`tests/e2e.py`）再发布：启动程序，用合成的鼠标事件在一个文本窗口里双击、拖选，点喇叭，然后核对取到的字数、释义、发音文件、播放状态、前台窗口有没有被抢走、剪贴板有没有还原。源码和打包后的程序各跑一遍，任何一项不过就不会发布。

| 组件 | 状态 |
|---|---|
| 桌面版 · Windows（Server 2025，exe） | 自动测试通过：启动提示、托盘图标和菜单、选词出喇叭、取词、发声（虚拟声卡，确认播放进度在走）、重复启动的对话框；另在「用户名和路径含中文、空格」的环境下跑通 |
| 桌面版 · macOS 15（Apple 芯片 / Intel，.app） | 自动测试通过：启动提示、选词出喇叭、取词、发声、Dock 重新打开和退出 |
| 桌面版 · Linux X11（Ubuntu 22.04，GTK） | 自动测试通过（`tests/run_linux.sh`，虚拟显示器）：启动提示、选中即识别、中文不触发、释义和发音下载；测试环境不接声卡，发声只核对了解码流程 |
| 扩展 · Chromium 156、Firefox 158（Linux） | 实测通过 |

没有覆盖到的：

- 上面的自动测试跑在 GitHub 的虚拟机上，**不是真人在自己的电脑上使用**。杀毒软件、SmartScreen、高分屏缩放（只测了 100%）、多显示器、各家 PDF 阅读器里的表现都没有测到。
- macOS 上测试机已经有「辅助功能」权限，「首次授权」这一步的流程没有被测到。
- Windows 7 / 8、Windows on ARM、纯 Wayland 会话（X11 会话和 XWayland 程序可用）不支持或未测试。

遇到问题请带上 `log.txt` 提 [Issue](../../issues)。

## 声明

发音和释义来自有道词典、百度翻译的网页接口，均为非官方用法，可能随时变动或失效，仅供个人学习使用。代码以 [MIT](LICENSE) 许可开源。
