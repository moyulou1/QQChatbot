# QQ AI 聊天机器人

> 一个功能完整、易于部署的 QQ 群聊 AI 机器人，接入 DeepSeek 大语言模型，支持聊天对话、图片识别、语音收发、长期记忆、人格切换等功能。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![NoneBot](https://img.shields.io/badge/NoneBot-2.5+-green.svg)](https://nonebot.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)](#)

---

## ✨ 功能特性

### 核心功能
- **💬 智能聊天**：接入 DeepSeek 大模型，支持上下文理解，像真人一样聊天
- **🖼️ 图片识别**：@机器人并发图，能识别图片内容并回答问题
- **🎤 语音收发**：群友发语音自动转文字，说"我想听"用合成语音回复
- **🧠 长期记忆**：自动记录群友信息，记住重要对话，越聊越懂你
- **🎭 人格切换**：YAML 格式人格文件，一键切换，支持自定义人格
- **🌐 联网搜索**：集成 Bing 搜索，能回答实时信息

### 管理功能
- **🖥️ Web 控制台**：图形化管理界面，状态监控、调试、Token 统计、配置管理
- **⚙️ 图形化配置工具**：零代码基础也能轻松配置，填表单就行
- **🚀 图形化启动器**：一键启动/停止，实时显示运行状态
- **📦 一键安装包**：双击安装，自动配置环境，小白也能用

### 安全与稳定
- **🛡️ 安全过滤**：输入输出双重过滤，防越狱、防敏感内容
- **⏱️ 速率限制**：单用户/单群双重限速，防止刷屏
- **🔄 会话级队列**：并发消息排队处理，不丢消息、不吞回复
- **✅ 人格一致性校验**：自动检测回复是否符合人格设定，防止 OOC
- **😢 情绪判定**：根据对话内容判断情绪，选择对应音色回复

---

## 🛠️ 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| 语言 | Python 3.10+ | 主开发语言 |
| 机器人框架 | NoneBot2 | 异步机器人框架 |
| 协议端 | NapCat | QQ NT 协议端，WebSocket 通信 |
| 大模型 | DeepSeek API | chat / reasoner / vision 三种模型 |
| Web 框架 | FastAPI + Uvicorn | 控制台后端 |
| 前端 | 原生 HTML/CSS/JS | 控制台前端，无框架依赖 |
| 数据库 | SQLite | 记忆、画像、Token 统计 |
| 语音识别 | faster-whisper | 本地 ASR，CPU 可跑 |
| 语音合成 | 多 Provider | 本地 GPT-SoVITS / Minimax / 火山引擎 / Fish Audio |
| 音视频处理 | ffmpeg | 音频转码 |
| 打包 | 7-Zip SFX | 自解压安装包 |

---

## 📦 快速开始

### 方式一：一键安装包（推荐普通用户）

1. 下载最新的 `QQAIChatbot_Setup_v*.exe`
2. 双击运行，选择安装路径
3. 等待解压完成，自动运行环境配置
4. 运行「配置工具」，填入你的 DeepSeek API Key 和 QQ 号
5. 运行「启动器」，点击「一键启动」
6. NapCat 弹出二维码，用机器人小号扫码登录
7. 开始使用！

> 详细说明请参考 [快速开始指南](docs/QUICK_START.md)

### 方式二：源码部署（推荐开发者）

#### 1. 环境要求
- Windows 10/11 64位
- Python 3.10+（安装时勾选 `Add Python to PATH`）
- QQ 账号（机器人小号）
- DeepSeek API Key（[注册地址](https://platform.deepseek.com/)）

#### 2. 克隆项目
```bash
git clone https://github.com/your-username/qq-ai-chatbot.git
cd qq-ai-chatbot
```

#### 3. 安装依赖
```bash
cd bot
pip install -r requirements.txt
```

#### 4. 配置
```bash
# 复制配置模板
copy .env.example .env

# 编辑 .env，填入以下内容：
# DEEPSEEK_API_KEY=你的API_Key
# SUPERUSERS=["你的QQ号"]
# NICKNAME=["机器人昵称"]
```

#### 5. 安装 NapCat
- 下载 NapCat：https://github.com/NapNeko/NapCatQQ
- 解压到 `napcat/` 目录
- 配置正向 WebSocket 连接到 `ws://127.0.0.1:8080/onebot/v11/ws`

#### 6. 启动
```bash
# 回到项目根目录
cd ..

# 启动（会自动启动后端 + NapCat）
start.bat
```

#### 7. 登录
- NapCat 窗口弹出二维码
- 用机器人小号扫码登录
- 登录成功后即可开始聊天

#### 8. 打开控制台
浏览器访问：http://127.0.0.1:18080
默认令牌：`admin123`

---

## 📁 项目结构

```
qq-ai-chatbot/
├── bot/                          # 核心代码
│   ├── plugins/
│   │   └── ai_chat/             # AI 聊天插件
│   │       ├── __init__.py      # 主消息处理器
│   │       ├── config.py        # 配置管理
│   │       ├── llm.py           # LLM 调用封装
│   │       ├── memory.py        # 长期记忆
│   │       ├── profiles.py      # 用户画像
│   │       ├── personality.py   # 人格管理
│   │       ├── personality_check.py  # 人格一致性校验
│   │       ├── vision.py        # 图片识别
│   │       ├── asr.py           # 语音识别
│   │       ├── tts.py           # 语音合成
│   │       ├── emotion.py       # 情绪判定
│   │       ├── voice_manager.py # 音色管理
│   │       ├── safety.py        # 安全过滤
│   │       ├── router.py        # 模型路由
│   │       ├── token_stats.py   # Token 统计
│   │       ├── message_queue.py # 会话级消息队列
│   │       ├── web_search.py    # 联网搜索
│   │       └── web_console.py   # Web 控制台后端
│   ├── web_static/              # 控制台前端
│   │   └── index.html
│   ├── bot.py                   # 入口文件
│   ├── requirements.txt         # Python 依赖
│   └── .env.example             # 配置模板
├── personalities/                # 人格文件
│   └── default.yaml             # 默认人格
├── tools/                        # 外部工具
│   ├── ffmpeg/                  # 音视频处理
│   └── silk/                    # QQ 语音编解码
├── docs/                         # 文档
│   ├── API.md                   # API 接口文档
│   ├── QUICK_START.md           # 快速开始指南
│   └── FAQ.md                   # 常见问题
├── data/                         # 运行时数据（自动生成）
├── logs/                         # 日志（自动生成）
├── models/                       # AI 模型缓存（自动下载）
├── 配置工具.py                    # 图形化配置工具
├── 启动器.py                      # 图形化启动器
├── 一键安装.bat                   # 一键安装脚本
├── start.bat                     # 启动脚本
├── stop.bat                      # 停止脚本
├── .gitignore
├── LICENSE
├── CONTRIBUTING.md
└── README.md
```

---

## ⚙️ 配置说明

所有配置都在 `bot/.env` 文件中，主要配置项：

### 基础配置
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（必填） | - |
| `SUPERUSERS` | 管理员 QQ 号列表 | `[]` |
| `NICKNAME` | 机器人昵称列表 | `["AI助手"]` |
| `PORT` | 后端监听端口 | `8080` |
| `CONSOLE_PORT` | 控制台端口 | `18080` |
| `CONSOLE_TOKEN` | 控制台登录令牌 | `admin123` |

### 回复配置
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `REPLY_ON_AT` | 被@时回复 | `true` |
| `REPLY_ON_NAME` | 提到昵称时回复 | `true` |
| `RANDOM_REPLY_PROBABILITY` | 随机插话概率 | `0.1` |
| `REPLY_COOLDOWN` | 回复冷却时间（秒） | `3` |
| `CONTEXT_ROUNDS` | 上下文轮数 | `8` |

### 模型配置
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `DEEPSEEK_MODEL` | 聊天模型 | `deepseek-chat` |
| `DEEPSEEK_REASONER_MODEL` | 推理模型 | `deepseek-reasoner` |
| `REASON_MODE` | 推理模式（auto/chat/reasoner） | `auto` |
| `DEEPSEEK_TEMPERATURE` | 温度参数 | `0.5` |
| `DEEPSEEK_MAX_TOKENS` | 最大 Token 数 | `2048` |

### 语音配置
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `ASR_ENABLED` | 启用语音识别 | `false` |
| `ASR_MODEL_SIZE` | ASR 模型大小（tiny/base/small） | `base` |
| `TTS_ENABLED` | 启用语音合成 | `false` |
| `TTS_PROVIDER` | TTS 引擎（local/minimax/volc/fish） | `minimax` |
| `TTS_TRIGGER_MODE` | 触发模式（on_demand/always） | `on_demand` |

> 完整配置说明请参考 [配置文档](docs/CONFIG.md)

---

## 📡 API 接口

Web 控制台提供 RESTful API，主要接口：

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 获取运行状态 |
| `/api/config` | GET/POST | 获取/更新配置 |
| `/api/personality/list` | GET | 获取人格列表 |
| `/api/personality/{name}/activate` | POST | 激活人格 |
| `/api/memory/query` | POST | 查询记忆 |
| `/api/token/stats` | GET | 获取 Token 统计 |
| `/api/debug/send` | POST | 发送调试消息 |

> 完整 API 文档请参考 [API 接口文档](docs/API.md)

---

## 🤝 贡献指南

欢迎贡献代码！请参考 [贡献指南](CONTRIBUTING.md)。

### 开发环境搭建
1. Fork 本仓库
2. 克隆到本地
3. 安装依赖：`cd bot && pip install -r requirements.txt`
4. 创建分支：`git checkout -b feature/your-feature`
5. 提交修改：`git commit -m 'Add some feature'`
6. 推送分支：`git push origin feature/your-feature`
7. 提交 Pull Request

### 代码规范
- 遵循 PEP 8 代码风格
- 添加必要的注释和文档字符串
- 提交前确保代码能正常运行
- 提交信息使用中文，清晰描述修改内容

---

## 📄 许可证

本项目基于 [MIT 许可证](LICENSE) 开源，你可以自由使用、修改、分发，包括商业用途。

---

## 🙏 致谢

- [NoneBot2](https://nonebot.dev/) - 优秀的 Python 机器人框架
- [NapCat](https://github.com/NapNeko/NapCatQQ) - QQ NT 协议端
- [DeepSeek](https://www.deepseek.com/) - 大语言模型
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) - 高效语音识别
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) - 语音合成
- [ffmpeg](https://ffmpeg.org/) - 音视频处理

---

## ⚠️ 免责声明

1. 本项目仅供学习交流使用，请勿用于非法用途
2. 使用 QQ 机器人可能违反 QQ 用户协议，使用风险自负
3. 建议使用小号作为机器人，避免主号被封
4. 本项目不承担任何因使用本软件造成的损失

---

## 📮 联系方式

- GitHub Issues：[提交问题](https://github.com/your-username/qq-ai-chatbot/issues)
- 邮箱：your-email@example.com

---

**如果这个项目对你有帮助，欢迎点个 Star ⭐ 支持一下！**
