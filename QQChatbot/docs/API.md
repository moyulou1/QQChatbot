# API 接口文档

> Web 控制台 RESTful API 文档
>
> 基础地址：`http://127.0.0.1:18080`
>
> 认证方式：请求头携带 `Authorization: Bearer <token>` 或 `X-Admin-Token: <token>`
>
> 默认令牌：`admin123`（可在 .env 中通过 `CONSOLE_TOKEN` 修改）

---

## 目录

- [认证](#认证)
- [基础接口](#基础接口)
- [Token 统计](#token-统计)
- [人格管理](#人格管理)
- [调试接口](#调试接口)
- [QQ 接口](#qq-接口)
- [配置管理](#配置管理)
- [控制接口](#控制接口)
- [用户画像](#用户画像)
- [日志接口](#日志接口)
- [音色管理](#音色管理)
- [情绪统计](#情绪统计)
- [错误码](#错误码)

---

## 认证

所有 `/api/*` 接口（除 `/api/health` 和 `/api/login` 外）都需要认证。

### 请求头

```
Authorization: Bearer admin123
```

或

```
X-Admin-Token: admin123
```

### 未认证响应

```json
{
  "detail": "Not authenticated"
}
```

HTTP 状态码：`401`

---

## 基础接口

### 健康检查

检查服务是否正常运行。

```
GET /api/health
```

**响应示例：**

```json
{
  "status": "ok",
  "timestamp": "2026-09-09T12:00:00"
}
```

---

### 登录

验证令牌并获取登录状态。

```
POST /api/login
```

**请求体：**

```json
{
  "token": "admin123"
}
```

**响应示例：**

```json
{
  "success": true,
  "message": "登录成功"
}
```

---

### 仪表盘

获取仪表盘汇总数据。

```
GET /api/dashboard
```

**响应示例：**

```json
{
  "status": "running",
  "uptime": 3600,
  "total_messages": 1234,
  "today_messages": 56,
  "total_tokens": 100000,
  "today_tokens": 5000,
  "active_groups": 3,
  "active_users": 15,
  "current_personality": "default",
  "reply_enabled": true
}
```

---

### 运行状态

获取详细运行状态。

```
GET /api/status
```

**响应示例：**

```json
{
  "running": true,
  "reply_enabled": true,
  "reason_mode": "auto",
  "current_personality": "default",
  "started_at": "2026-09-09T10:00:00",
  "paused_at": null,
  "bot_connected": true,
  "napcat_connected": true,
  "queue_stats": {
    "active_sessions": 2,
    "pending_messages": 0,
    "processed_today": 56
  }
}
```

---

## Token 统计

### 今日 Token 消耗

```
GET /api/token/today
```

**响应示例：**

```json
{
  "date": "2026-09-09",
  "total_tokens": 5000,
  "prompt_tokens": 4000,
  "completion_tokens": 1000,
  "estimated_cost": 0.05,
  "request_count": 56
}
```

---

### 历史 Token 消耗

```
GET /api/token/history?days=30
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `days` | int | 30 | 查询天数 |

**响应示例：**

```json
{
  "days": [
    {"date": "2026-09-09", "total_tokens": 5000, "request_count": 56},
    {"date": "2026-09-08", "total_tokens": 8000, "request_count": 89}
  ],
  "summary": {
    "total_tokens": 100000,
    "total_requests": 1234,
    "avg_daily_tokens": 3333
  }
}
```

---

### 最近 Token 记录

```
GET /api/token/recent?limit=50
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 50 | 返回条数 |

**响应示例：**

```json
{
  "records": [
    {
      "timestamp": "2026-09-09T12:00:00",
      "group_id": "123456789",
      "user_id": "987654321",
      "model": "deepseek-chat",
      "prompt_tokens": 100,
      "completion_tokens": 50,
      "total_tokens": 150,
      "latency_ms": 1200
    }
  ]
}
```

---

### 按群统计 Token

```
GET /api/token/group/{group_id}?days=7
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `group_id` | string | - | 群号 |
| `days` | int | 7 | 查询天数 |

**响应示例：**

```json
{
  "group_id": "123456789",
  "days": 7,
  "total_tokens": 20000,
  "total_requests": 200,
  "daily": [
    {"date": "2026-09-09", "tokens": 5000, "requests": 56}
  ]
}
```

---

## 人格管理

### 人格列表

```
GET /api/personalities
```

**响应示例：**

```json
{
  "personalities": [
    {
      "name": "default",
      "filename": "default.yaml",
      "description": "通用AI助手，活泼可爱，乐于助人",
      "aliases": ["AI助手", "小助手", "bot"],
      "active": true,
      "modified_at": "2026-09-09T10:00:00"
    }
  ],
  "current": "default"
}
```

---

### 人格详情

```
GET /api/personality/{name}
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | string | 人格名称 |

**响应示例：**

```json
{
  "name": "default",
  "filename": "default.yaml",
  "content": "name: AI助手\ndescription: ...\nsystem_prompt: |\n  ...",
  "parsed": {
    "name": "AI助手",
    "description": "通用AI助手",
    "aliases": ["AI助手"],
    "temperature": 0.7,
    "max_tokens": 2048
  }
}
```

---

### 保存人格

创建或更新人格文件。

```
POST /api/personality/{name}
```

**请求体：**

```json
{
  "content": "name: 自定义人格\ndescription: 我的自定义人格\nsystem_prompt: |\n  你是一个自定义人格...\ntemperature: 0.8"
}
```

**响应示例：**

```json
{
  "success": true,
  "name": "custom",
  "message": "人格保存成功"
}
```

---

### 激活人格

切换当前使用的人格。

```
POST /api/personality/{name}/activate
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | string | 人格名称 |

**响应示例：**

```json
{
  "success": true,
  "previous": "default",
  "current": "custom",
  "message": "人格已切换为 custom"
}
```

---

### 删除人格

```
DELETE /api/personality/{name}
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | string | 人格名称 |

**响应示例：**

```json
{
  "success": true,
  "message": "人格已删除"
}
```

> 注意：不能删除当前激活的人格，需先切换到其他人格。

---

## 调试接口

### 发送调试消息

模拟群聊消息，测试 AI 回复。

```
POST /api/debug/send
```

**请求体：**

```json
{
  "message": "你好",
  "group_id": "123456789",
  "user_id": "987654321",
  "nickname": "测试用户"
}
```

**响应示例：**

```json
{
  "success": true,
  "reply": "你好呀！有什么可以帮你的吗？",
  "model": "deepseek-chat",
  "route": "chat",
  "reasoning": false,
  "tokens": {
    "prompt": 100,
    "completion": 20,
    "total": 120
  },
  "latency_ms": 1200
}
```

---

### 查询短期记忆

```
GET /api/debug/memory/{group_id}
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `group_id` | string | 群号 |

**响应示例：**

```json
{
  "group_id": "123456789",
  "messages": [
    {
      "role": "user",
      "content": "你好",
      "user_id": "987654321",
      "timestamp": "2026-09-09T12:00:00"
    },
    {
      "role": "assistant",
      "content": "你好呀！",
      "timestamp": "2026-09-09T12:00:01"
    }
  ],
  "count": 2
}
```

---

### 清除记忆

```
POST /api/debug/memory/clear
```

**请求体：**

```json
{
  "group_id": "123456789",
  "user_id": null
}
```

> `user_id` 为 null 时清除整个群的记忆。

**响应示例：**

```json
{
  "success": true,
  "message": "记忆已清除",
  "cleared_count": 10
}
```

---

### 查询长期记忆

```
GET /api/debug/long_memory/{group_id}?limit=20
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `group_id` | string | - | 群号 |
| `limit` | int | 20 | 返回条数 |

**响应示例：**

```json
{
  "group_id": "123456789",
  "memories": [
    {
      "id": 1,
      "content": "用户张三喜欢打篮球",
      "user_id": "987654321",
      "created_at": "2026-09-01T10:00:00",
      "importance": 0.8
    }
  ],
  "count": 1
}
```

---

## QQ 接口

### 群列表

获取机器人所在的群列表。

```
GET /api/qq/groups
```

**响应示例：**

```json
{
  "groups": [
    {
      "group_id": "123456789",
      "group_name": "测试群",
      "member_count": 50,
      "max_member_count": 200
    }
  ],
  "count": 1
}
```

---

### 群信息

```
GET /api/qq/group/{group_id}
```

**响应示例：**

```json
{
  "group_id": "123456789",
  "group_name": "测试群",
  "member_count": 50,
  "max_member_count": 200,
  "owner_id": "987654321",
  "admin_list": ["987654321"]
}
```

---

### 发送 QQ 消息

```
POST /api/qq/send
```

**请求体：**

```json
{
  "target_type": "group",
  "target_id": "123456789",
  "message": "这是一条测试消息"
}
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_type` | string | `group` 或 `private` |
| `target_id` | string | 群号或 QQ 号 |
| `message` | string | 消息内容 |

**响应示例：**

```json
{
  "success": true,
  "message_id": 12345,
  "message": "消息发送成功"
}
```

---

## 配置管理

### 获取配置

```
GET /api/config
```

**响应示例：**

```json
{
  "deepseek": {
    "api_key": "sk-****2367",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "reasoner_model": "deepseek-reasoner",
    "reason_mode": "auto",
    "temperature": 0.5,
    "max_tokens": 2048
  },
  "reply": {
    "reply_on_at": true,
    "reply_on_name": true,
    "random_reply_probability": 0.1,
    "reply_cooldown": 3,
    "context_rounds": 8
  },
  "personality": {
    "current": "default",
    "dir": "../personalities"
  },
  "vision": {
    "enabled": true,
    "require_trigger": true
  },
  "asr": {
    "enabled": false,
    "model_size": "base"
  },
  "tts": {
    "enabled": false,
    "provider": "minimax",
    "trigger_mode": "on_demand"
  },
  "console": {
    "port": 18080,
    "token": "admin123"
  }
}
```

> 注意：API Key 会被脱敏显示（只显示前 4 位和后 4 位）。

---

### 更新配置

```
POST /api/config/update
```

**请求体：**

```json
{
  "deepseek": {
    "temperature": 0.7,
    "max_tokens": 4096
  },
  "reply": {
    "random_reply_probability": 0.2
  }
}
```

> 只需要传入要修改的配置项，未传入的保持不变。

**响应示例：**

```json
{
  "success": true,
  "message": "配置已更新",
  "updated": {
    "deepseek.temperature": 0.7,
    "deepseek.max_tokens": 4096,
    "reply.random_reply_probability": 0.2
  }
}
```

---

## 控制接口

### 运行控制

暂停、恢复或重启机器人。

```
POST /api/control
```

**请求体：**

```json
{
  "action": "pause"
}
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `action` | string | `pause`（暂停）、`resume`（恢复）、`restart`（重启） |

**响应示例：**

```json
{
  "success": true,
  "action": "pause",
  "message": "机器人已暂停",
  "state": {
    "reply_enabled": false,
    "paused_at": "2026-09-09T12:00:00"
  }
}
```

---

### LLM 测试

测试 DeepSeek API 连接。

```
POST /api/llm/test
```

**请求体：**

```json
{
  "api_key": "sk-your-api-key",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat"
}
```

> 不传参数时使用当前配置测试。

**响应示例：**

```json
{
  "success": true,
  "message": "连接成功",
  "latency_ms": 500,
  "reply": "你好！我是 DeepSeek AI 助手。"
}
```

---

## 用户画像

### 画像列表

```
GET /api/profiles?group_id=
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `group_id` | string | 空 | 群号，为空时返回所有 |

**响应示例：**

```json
{
  "profiles": [
    {
      "user_id": "987654321",
      "group_id": "123456789",
      "nickname": "张三",
      "name": "张三",
      "age": 25,
      "gender": "男",
      "location": "北京",
      "hobbies": ["篮球", "编程"],
      "personality": "开朗",
      "notes": "喜欢讨论技术问题",
      "updated_at": "2026-09-09T10:00:00"
    }
  ],
  "count": 1
}
```

---

### 更新画像

```
POST /api/profiles/update
```

**请求体：**

```json
{
  "user_id": "987654321",
  "group_id": "123456789",
  "nickname": "张三",
  "name": "张三",
  "age": 26,
  "gender": "男",
  "hobbies": ["篮球", "编程", "音乐"]
}
```

**响应示例：**

```json
{
  "success": true,
  "message": "画像已更新"
}
```

---

### 删除画像

```
POST /api/profiles/delete
```

**请求体：**

```json
{
  "user_id": "987654321",
  "group_id": "123456789"
}
```

**响应示例：**

```json
{
  "success": true,
  "message": "画像已删除"
}
```

---

## 日志接口

### 获取日志

```
GET /api/logs?limit=300&level=
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 300 | 返回条数 |
| `level` | string | 空 | 日志级别过滤（DEBUG/INFO/WARNING/ERROR） |

**响应示例：**

```json
{
  "logs": [
    {
      "timestamp": "2026-09-09T12:00:00",
      "level": "INFO",
      "message": "收到群消息: 你好",
      "source": "ai_chat"
    }
  ],
  "count": 1
}
```

---

### 实时日志（WebSocket）

```
WS /ws/logs
```

**连接后实时推送日志：**

```json
{
  "timestamp": "2026-09-09T12:00:00",
  "level": "INFO",
  "message": "收到群消息: 你好",
  "source": "ai_chat"
}
```

---

## 音色管理

### 音色列表

```
GET /api/voices?character=
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `character` | string | 空 | 角色名过滤 |

**响应示例：**

```json
{
  "voices": [
    {
      "id": "default_calm",
      "name": "平静",
      "character": "default",
      "emotion": "calm",
      "ref_audio": "voices/default/calm.wav",
      "ref_text": "这是一段参考音频文本",
      "pitch": 0,
      "speed": 1.0
    }
  ],
  "count": 1
}
```

---

### 获取音色配置

```
GET /api/voices/config
```

**响应示例：**

```json
{
  "enabled": false,
  "default_character": "default",
  "pitch_shift": true,
  "characters": ["default"]
}
```

---

### 更新音色配置

```
POST /api/voices/config
```

**请求体：**

```json
{
  "enabled": true,
  "default_character": "default",
  "pitch_shift": true
}
```

**响应示例：**

```json
{
  "success": true,
  "message": "音色配置已更新"
}
```

---

### 上传参考音频

```
POST /api/voices/upload
```

**请求体（multipart/form-data）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `character` | string | 角色名 |
| `emotion` | string | 情绪标签 |
| `ref_text` | string | 参考音频文本 |
| `audio` | file | 音频文件（wav/mp3） |

**响应示例：**

```json
{
  "success": true,
  "voice_id": "default_happy",
  "message": "音色上传成功"
}
```

---

## 情绪统计

### 获取情绪统计

```
GET /api/emotion/stats
```

**响应示例：**

```json
{
  "total": 1000,
  "distribution": {
    "happy": 350,
    "calm": 300,
    "sad": 100,
    "angry": 50,
    "surprised": 100,
    "neutral": 100
  },
  "recent": [
    {
      "timestamp": "2026-09-09T12:00:00",
      "emotion": "happy",
      "confidence": 0.92,
      "group_id": "123456789"
    }
  ]
}
```

---

## 错误码

| HTTP 状态码 | 说明 |
|-------------|------|
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 401 | 未认证或令牌无效 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 429 | 请求过于频繁 |
| 500 | 服务器内部错误 |

### 错误响应格式

```json
{
  "detail": "错误描述",
  "code": "ERROR_CODE"
}
```

---

## 使用示例

### cURL

```bash
# 获取状态
curl -H "Authorization: Bearer admin123" http://127.0.0.1:18080/api/status

# 发送调试消息
curl -X POST http://127.0.0.1:18080/api/debug/send \
  -H "Authorization: Bearer admin123" \
  -H "Content-Type: application/json" \
  -d '{"message":"你好","group_id":"123456789","user_id":"987654321"}'

# 切换人格
curl -X POST http://127.0.0.1:18080/api/personality/default/activate \
  -H "Authorization: Bearer admin123"
```

### Python

```python
import requests

BASE_URL = "http://127.0.0.1:18080"
HEADERS = {"Authorization": "Bearer admin123"}

# 获取状态
resp = requests.get(f"{BASE_URL}/api/status", headers=HEADERS)
print(resp.json())

# 发送调试消息
resp = requests.post(
    f"{BASE_URL}/api/debug/send",
    headers=HEADERS,
    json={"message": "你好", "group_id": "123456789", "user_id": "987654321"}
)
print(resp.json())
```

---

## 更新日志

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0.0 | 2026-09-09 | 初始版本，包含全部基础 API |

---

**如有问题请提交 GitHub Issue。**
