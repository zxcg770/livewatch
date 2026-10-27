# LiveWatch · AI 盯播工具

实时识别主播讲解内容，及时发现直播中的问题：**语速 / 节奏 / 冷场**（本地实时计算）、**违禁词**（秒级告警）、**话术缺失与合规风险**（大模型每分钟对照话术框架分析最近 5 分钟）。

跨平台桌面应用（macOS / Windows），Python 后端 + Web 界面 + pywebview 原生窗口。Windows 上 pywebview 使用系统自带的 Edge WebView2（Win10/11 一般已预装）；macOS 已实测，Windows 只做了兼容性处理和模拟验证，尚未在真机上运行。

## 功能

- **盯任意直播间**：粘贴抖音直播间链接即可；数据源可扩展；本地音视频文件可按倍速回放（离线演示 / 回归测试）
- **多直播间并发**：每个直播间一个独立会话，标签页切换，未读告警计数
- **实时转写**：SenseVoice 本地识别（无需联网、免费），附带情绪和背景音乐识别；品牌名纠错表
- **表达指标**：语速（字/分）、讲话占比、最长冷场、口头禅、重复度，阈值可在框架中配置
- **违禁词**：广告法极限词、化妆品功效夸大、医疗用语、站外引流 + 框架自定义词
- **AI 话术分析**：每 N 秒分析最近 M 分钟（均可设置），输出阶段覆盖、问题清单（附原文证据和给主播的提示）、话术评分
- **在线人数曲线**：每分钟取一次直播间在线人数，和语速画在同一张图上，并在时间轴上标出告警，看问题出现后观众有没有流失；录制样本时同步记录人数，回放也能看
- **告警回听**：每条告警可一键播放对应原声（违禁词和 AI 引用按字定位到 5～10 秒的片段），快速核实是主播说错还是识别错
- **告警去重**：同一问题在一个窗口内只提醒一次，重复出现累加次数，避免滑动窗口重叠导致刷屏
- **话术框架编辑**：YAML 格式，界面内直接编辑
- **场次复盘报告** + 全部数据落盘（`sessions/`）

## 快速开始

依赖：Python ≥ 3.11、[uv](https://docs.astral.sh/uv/)。ffmpeg 可选：系统里装了就用系统的，没有就自动用 `imageio-ffmpeg` 自带的版本，Windows 上不需要另外安装。

```bash
# 1. 安装依赖
uv sync

# 2. 下载识别模型（SenseVoice int8 约 163MB + Silero VAD）
uv run scripts/download_models.py   # Mac / Windows 通用

# 3. 配置大模型（可选，不配置则只运行本地规则）
cp .env.example .env   # 填入 LLM_API_KEY，默认 DeepSeek，兼容任意 OpenAI 接口

# 4. 启动桌面应用
uv run app.py
# 或用浏览器打开（调试）
uv run app.py --browser
```

演示建议：直播间没开播时，输入框填 `samples/xxx.wav`，倍速设为 10，窗口 5 分钟、每 60 秒分析。

## 命令行工具

```bash
uv run scripts/record.py https://live.douyin.com/295178185857 --minutes 30   # 录制样本（同时记录在线人数）
uv run scripts/transcribe.py samples/xxx.wav --minutes 3                     # 离线转写 + 测速
```

## 架构

```
直播间 URL / 本地文件
   │ StreamSource.resolve()                  sources/    （抖音 / 文件，可扩展）
   ▼
ffmpeg → 16kHz 单声道 PCM（100ms 一块）        audio.py    （断流重新解析地址 + 指数退避重连）
   ▼
Silero VAD 切句 → SenseVoice 识别             asr.py      （本地，比实时快约 30 倍）
   ▼
纠错表 → 违禁词检测（每句，秒级）                rules.py
   ▼
每 interval 秒（按音频时间）：
   ├─ 本地指标（最近一个周期）→ 表达类告警        metrics.py
   └─ LLM 分析（最近 window 分钟 + 框架）→ 话术类告警   analyzer.py
   ▼
告警去重 → WebSocket → 界面                    session.py / server.py / web/index.html
```

| 目录 | 说明 |
|---|---|
| `app.py` | 桌面入口：后台线程起 uvicorn，pywebview 打开原生窗口 |
| `livewatch/` | 核心模块（见上图） |
| `config/frameworks/` | 话术框架 YAML |
| `web/index.html` | 界面（单文件，无构建） |
| `scripts/` | 录制、转写、模型下载 |
| `docs/devlog.md` | 开发日志（问题与取舍） |
| `docs/presentation.md` | 汇报提纲 |
