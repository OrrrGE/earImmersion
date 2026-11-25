# earImmersion（磨耳朵）· 划词发音

选中英文 → 出现小喇叭 → 点击听标准发音，旁边显示音标和中文释义。
单词、缩写（PLAID）、多个词、整句都可以，多个词会连起来读。

两种形态，按需选用，可以同时装：

| | 浏览器扩展 `extension/` | 桌面版 `desktop/speak.py` |
|---|---|---|
| 适用 | 网页、arXiv HTML、在线 PDF.js 阅读器 | 任何软件：PDF 阅读器、Zotero、Word、浏览器内置 PDF |
| 体积 | 3 个文件共 10 KB | 1 个文件 25 KB |
| 依赖 | 无 | 只需 Python 3（无第三方库） |
| 平台 | Chrome / Edge / Firefox（Win / Mac / Linux） | Windows / macOS / Linux(X11) |

## 浏览器扩展

**Chrome / Edge**：打开 `chrome://extensions` → 打开右上角「开发者模式」→「加载已解压的扩展程序」→ 选 `extension` 文件夹。

**Firefox**：打开 `about:debugging#/runtime/this-firefox` →「临时载入附加组件」→ 选 `extension/manifest.json`（重启 Firefox 后需重新载入）。

装好后**已经打开的页面要刷新一次**才生效。浏览器内置的 PDF 查看器不允许扩展介入，读本地 PDF 请用桌面版。

## 桌面版

### 免安装下载（Windows / macOS）

到 [Releases](../../releases/latest) 下载，不需要装 Python：

- **Windows**：`earImmersion-windows-x64.exe`，双击运行（没有窗口，常驻后台；退出请在任务管理器里结束 earImmersion）。首次运行 SmartScreen 可能拦截，点「更多信息 → 仍要运行」。
- **macOS**：Apple 芯片下载 `earImmersion-macos-arm64.zip`，Intel 下载 `earImmersion-macos-x64.zip`。解压后把 `earImmersion.app` 拖进「应用程序」。应用没有 Apple 签名，首次需要右键 →「打开」（或执行 `xattr -cr /Applications/earImmersion.app`），并在「系统设置 → 隐私与安全性 → 辅助功能」里勾选 earImmersion。没有 Dock 图标，退出请用「活动监视器」。

### 从源码运行

| 系统 | 启动 | 备注 |
|---|---|---|
| Linux | `python3 desktop/speak.py` | 用系统自带的 GTK；在 conda 环境里运行会自动换成 `/usr/bin/python3` |
| Windows | `pythonw desktop\speak.py` | 需要 python.org 的 Python 3 |
| macOS | `python3 desktop/speak.py` | 需要 python.org 的 Python 3；首次使用要在「系统设置 → 隐私与安全性 → 辅助功能」里允许终端 / Python |

用法：拖选或双击选词，鼠标旁出现喇叭；鼠标移上去就开始预取，点击即播放，再点一次重听。点别处或 4 秒不理它会自动消失。

- **Linux**：选中即识别内容，非英文（如中文）不会弹喇叭。
- **Windows / macOS**：系统没有「主选区」，喇叭先出现，**点击时**才模拟一次 Ctrl+C / ⌘C 取词，随后还原剪贴板里原有的文本。Windows 上只在鼠标为「I」形文本光标时才弹，避免拖窗口误触发；如果某个软件里不弹，把 `speak.py` 顶部的 `IBEAM_ONLY` 改成 `False`。

开机自启：
- Linux：新建 `~/.config/autostart/moerduo.desktop`，内容为
  ```
  [Desktop Entry]
  Type=Application
  Name=磨耳朵
  Exec=/usr/bin/python3 /你的路径/earImmersion/desktop/speak.py
  ```
- Windows：`Win+R` 输入 `shell:startup`，在打开的文件夹里放一个指向 `pythonw.exe 路径\speak.py` 的快捷方式。
- macOS：「系统设置 → 通用 → 登录项」里添加一个执行上述命令的脚本。

## 配置

改文件顶部的常量即可：

- `ACCENT`：`2` 美音（默认），`1` 英音。扩展在 `extension/bg.js`，桌面版在 `desktop/speak.py`。
- `MAX_LEN`：选中内容超过多少字符就不弹（默认 300）。

## 工作方式

- 发音：优先有道词典真人发音；有道没有收录的短语 / 句子用百度语音合成。
- 释义：有道词典（单词给音标 + 词性释义，短语和句子给翻译；几个不相关的词一起选时逐词给释义）。
- 桌面版把发音缓存在 `~/.cache/moerduo/`（最多 1000 个文件），听过的词再点是瞬时的。
- 隐私：只有鼠标移到喇叭上或点击时，才会把**选中的那段文本**发给有道 / 百度；仅仅选中不会联网。

## 测试情况

| 组件 | 状态 |
|---|---|
| 扩展 · Chromium 156（Linux） | 实测通过 |
| 扩展 · Firefox 158（Linux） | 实测通过 |
| 桌面版 · Linux X11（GTK） | 实测通过 |
| 桌面版 · Windows / macOS 的弹窗与主流程 | 在 Linux 上用模拟取词跑通 |
| 桌面版 · Windows / macOS 的系统调用（鼠标状态、模拟复制、不抢焦点、播放） | 仅在 CI 机器上做过冒烟测试，**未经真人在真机上使用验证** |

纯 Wayland 会话暂不支持桌面版（X11 会话和 XWayland 程序可用）。

## 声明

发音和释义来自有道词典、百度翻译的网页接口，均为非官方用法，可能随时变动或失效，仅供个人学习使用。代码以 [MIT](LICENSE) 许可开源。
