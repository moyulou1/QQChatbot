#!/usr/bin/env python3
"""
QQ群聊AI自动回复机器人 - 入口文件
基于 NoneBot2 + OneBot v11 协议
插件目录在 pyproject.toml 的 [tool.nonebot] 中配置
适配器必须在代码中手动注册（load_from_toml 不处理适配器）
"""
import os
import sys

# ========== 强制所有写入到 F 盘（入口级，确保插件加载前生效）==========
_F_DISK_TMP = r"F:\qq_chatbot\tmp"
_F_DISK_LOG = r"F:\qq_chatbot\logs"
os.makedirs(_F_DISK_TMP, exist_ok=True)
os.makedirs(_F_DISK_LOG, exist_ok=True)
os.environ["TEMP"] = _F_DISK_TMP
os.environ["TMP"] = _F_DISK_TMP
import tempfile
tempfile.tempdir = _F_DISK_TMP
# ==================================================================

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from loguru import logger

# 初始化 NoneBot（自动读取 .env）
nonebot.init()

# ========== 文件日志轮转（F盘，按10MB轮转，保留5个，UTF-8编码）==========
_log_file = os.path.join(_F_DISK_LOG, "bot_boot.log")
logger.add(
    _log_file,
    rotation="10 MB",       # 单个文件超过10MB自动轮转
    retention=5,             # 保留最近5个日志文件
    encoding="utf-8",
    enqueue=True,            # 异步写入，避免阻塞
    backtrace=True,
    diagnose=False,
    level="INFO",
)
logger.info(f"[Boot] 日志轮转已启用: {_log_file} (10MB/个, 保留5个)")
logger.info(f"[Boot] 临时目录: {tempfile.gettempdir()}")
# ==================================================================

# 注册 OneBot v11 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 从 pyproject.toml 加载插件目录和内置插件
nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
