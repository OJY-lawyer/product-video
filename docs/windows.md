# Windows 安装与使用

Windows 适配保留 Python → Remotion / Shotcraft → 可编辑工作台 → MP4/SRT 的主流程。默认 16:9、1920×1080、30 fps；未扩展竖屏。macOS 入口和原生采集继续保留。

## 安装

准备已有 Python 3.11+、Node.js 22+（含 npm）、FFmpeg 与 ffprobe。不要为本项目改系统运行库或公共 PATH。以下路径是示例，应替换为本机已确认路径：

```powershell
& 'D:/tools/product-video/scripts/setup.ps1' `
  -Python 'D:/runtimes/python/python.exe' `
  -Node 'D:/runtimes/node/node.exe' `
  -FfmpegDirectory 'D:/tools/ffmpeg/bin'
```

脚本将 Python 依赖装入 `scripts/engine/.venv`，Node 依赖装入 `vendor/video-shotcraft/workbench/node_modules`，Playwright 浏览器装入 `.cache/playwright`。Remotion 浏览器通过项目 Remotion CLI 准备；也可传 `-BrowserExecutable '.../chrome.exe'` 使用明确指定的兼容浏览器。`-SkipBrowsers` 仅跳过浏览器准备，不能据此声明网页采集或渲染已可用。脚本不调用语音服务，不读取或设置配音密钥。

本地运行路径保存在 `.runtime.local.json`，后续安装会沿用，`run.ps1` 仅在本次进程中应用。该文件不得提交或分发。无管理员权限的 Windows 使用目录 Junction 接入库素材；不需要开启开发者模式或创建文件符号链接。

复用已有 Playwright 浏览器/录屏辅助程序时，加 `-PlaywrightBrowsersPath '已核实的浏览器缓存目录' -SkipBrowsers`；外部缓存只读复用，不在其中安装或清理。`-BrowserExecutable` 同时用于默认网页采集、三维预渲染与 Remotion；显式网页 `channel: chrome/msedge` 仍遵从该选择。网页录屏还需现有 Playwright ffmpeg 辅助程序，不能只检查 Chrome 文件就宣称录屏可用。

```powershell
& 'D:/tools/product-video/scripts/run.ps1' --help
& 'D:/tools/product-video/scripts/run.ps1' check 'D:/视频项目/产品 demo/project.json'
& 'D:/tools/product-video/scripts/run.ps1' auto 'D:/视频项目/产品 demo/project.json'
& 'D:/tools/product-video/scripts/run.ps1' studio 'D:/视频项目/产品 demo/project.json'
```

终端里的运行库路径参数与项目路径均按独立参数传入，支持中文和空格。运行命令在当前目录解析相对项目参数；所有素材路径相对项目 JSON。

## 纯字幕项目

可先用 `scripts/run.ps1 init 'D:/视频项目/结构样例' --silent` 创建无需密钥的结构示例（每章六秒）。其中界面是示例图，不能当作目标产品的真实功能证据。

这是无需密钥的正式制作模式，不生成假配音缓存。例子中的截图应替换为用户授权的真实界面；两章合计 12 秒：

```json
{
  "schema_version": 2,
  "product": {"name": "产品演示"},
  "output": "output",
  "voice": {"mode": "none"},
  "video": {"width": 1920, "height": 1080, "fps": 30, "subtitles": "auto"},
  "chapters": [
    {
      "id": "overview", "title": "工作总览", "duration": 6,
      "narration": "在同一页面查看当前工作。",
      "captions": [{"start": 0, "end": 6, "text": "在同一页面查看当前工作。"}],
      "steps": [{"at": 0, "images": ["assets/overview.png"]}]
    },
    {
      "id": "detail", "title": "查看详情", "duration": 6,
      "narration": "打开条目查看资料。继续处理下一步工作。",
      "steps": [{"at": 0, "images": ["assets/detail.png"]}]
    }
  ]
}
```

- `duration` 是该章总秒数，每章向上取整至完整帧，不再额外增加 `chapter_pause` 或配音前导时间。
- `captions` 为章内秒数，必须按时间排序、互不重叠且结束不超过 duration。未提供时，按文稿分句分配显示时间；这是显示时间安排，不是语音对齐。
- `steps[].at` 仍为本章时长比例。普通截图、文字内容、editorial、Shotcraft 和 3D 镜头可混排。
- `render` 和 `prepare-motion` 不调用 TTS；`auto` / `build` 在本模式同样不访问凭据或语音 API。显式执行 `voice` 或 `--voice` 不会自动开启配音，而会提示项目仍为无配音模式。
- 工作台“旁白与字幕”面板在本模式展示文稿和时长，主动作是“更新字幕与镜头”；字幕轨可继续编辑并导出。修改文稿会重建该章字幕显示时间，未改动的自定义 captions 保留。
- MP4 保留兼容的静音 AAC 音轨，旁白轨无片段。指定 `audio.bgm` / `audio.sfx` 时会混合这些音源，不能再将成片称为完全静音。

`output/latest.json` 指向已完整解码验证的 MP4、报告与可编辑 `studio/project.json`；同目录有 `subtitles.srt`、`narration.txt`、`project.resolved.json`、`timeline.json`。导出后仍需看图/播放检查界面、字幕可读性与连贯性。

## 原生窗口与网页

Windows 内置采集只读截取明确 PID/HWND 的独立顶层窗口。提供窗口列表和精确标题等待，**不提供 UIA 控件读写、鼠标键盘输入或桌面录屏**。不会启动、恢复、前置、关闭目标程序；不会失败后改截整个桌面。

```powershell
& 'D:/tools/product-video/scripts/run.ps1' list-windows --pid 1234
& 'D:/tools/product-video/scripts/run.ps1' inspect-app --provider windows --pid 1234 --window-handle 5678 --window-title '演示窗口'
```

PID/HWND 必须来自当前授权目标，不能照抄示例数字或只按标题选取。计划格式与拒绝条件见 [Windows 采集计划](../references/capture.md#windows-计划)。网页使用跨平台 Playwright，支持真实输入、点击、滚动与 `record-web`；已有录屏可导入三维屏幕或 Shotcraft 媒体。需要桌面交互时，只能使用宿主当前提供且授权的工具；本模块不能替代或绕过其权限。

## 字体、渲染与凭据

Windows 自动查找 `%WINDIR%/Fonts/msyh.ttc` 和 `simhei.ttf`；自定义字体用 `video.font`，或进程变量 `PRODUCT_VIDEO_FONT`。字体用于预渲染和 Remotion 工程，但字体许可不随本 Skill 转授，分享工程时自行核对字体分发权限。库里不打包系统字体。

Remotion 默认软件 WebGL `swangle`，三维预渲染默认 SwiftShader；需要性能调优时可显式设 `PRODUCT_VIDEO_GL` / `PRODUCT_VIDEO_3D_ANGLE`，不得把渲染成功等同硬件加速通过。`PRODUCT_VIDEO_NODE`、`PRODUCT_VIDEO_FFMPEG`、`PRODUCT_VIDEO_FFPROBE` 和 `PRODUCT_VIDEO_BROWSER` 支持进程内覆盖工具路径。Windows 编码使用 `libx264`，VideoToolbox 仅适用于 macOS。

需要配音时设 `voice.mode: "tts"`（缺省模式）并移除章 duration，沿用已授权的配音设置。Windows 凭据存于原有用户目录，使用当前用户 DPAPI 加密；不接受未保护的明文密钥文件。macOS/POSIX 沿用所有者与 0600 检查。`credentials status` 只检查文件元数据，不能证明账户额度或 API 可用。本次无配音工作无需打开凭据页面。

## 分发与验收

分发整个 Skill，包含 `vendor/video-shotcraft` 的源码、素材、目录和许可证；不要只复制 SKILL.md 与 scripts。使用 `scripts/package.py` 输出到源码目录之外，排除 `.venv`、node_modules、浏览器缓存、本地配置、work、outputs 和个人工程。目标机器重新安装项目依赖。

开发检查与独立验收分开记录。必要矩阵：Python 行为测试；工作台 build/integration；中文且含空格目录中的无配音 auto/build → MP4+SRT+时间轴；真实 Remotion 播放/复核；可选隔离合成窗口的目标截图。测试锁与拒绝最小化的逻辑不等同真实受保护窗口截图，离线模拟 TTS 不等同真实账户可用。macOS/Linux 兼容代码保留，不从 Windows 测试推导其他系统已验收。
