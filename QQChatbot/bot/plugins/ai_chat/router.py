"""
模型智能路由（单一事实源，群聊与调试台共用）
- auto：数学/逻辑/代码/专业求解类问题自动用 deepseek-reasoner 深度思考，其余用轻快的 chat
- chat：始终通用模型；reasoner：始终推理模型
"""
from __future__ import annotations

import re

_REASON_RE = re.compile(
    r"(计算|算一下|算算|算个|算一?算|怎么算|如何算|怎样算|等于|几加|几减|几乘|几除|"
    r"加起来|加上|减去|减掉|乘以|乘上|除以|除上|方程|求解|解一下|解这|解道|证明|推导|"
    r"概率|积分|导数|几何|函数|公式|算法|逻辑|原理|为什么|啥原理|区别|步骤|分步|一步步|"
    r"怎么实现|代码|程序|报错|bug|翻译|语法|几倍|百分比|利率|换算|统计|排列组合|求阴影|求证|"
    r"周长|面积|体积|表面积|边长|半径|直径|角度|路程|速度|单价|总价|平均数|应用题|"
    r"这道题|那道题|这题|那题|题目|做题|解题|数学题|奥数|数列|找规律|假设|未知数|因式|不等式|"
    r"打折|折扣|满减|优惠后|打[\d.]+折|原价)")
_MATH_SYMBOL_RE = re.compile(r"\d\s*[+\-*/×÷^=><≤≥]\s*\d")
_CN_ARITH_RE = re.compile(r"\d+\s*(加|减|乘|除|乘以|除以|加上|减去)\s*\d+")
_NUM_RE = re.compile(r"\d")
_SOLVE_Q_RE = re.compile(r"(多少|几|怎么|如何|何解|求解)")
_ACADEMIC_CTX_RE = re.compile(r"(题|算|数学|物理|化学|方程|周长|面积|体积|速度|路程|单价|求)")


def needs_reasoning(text: str) -> bool:
    t = (text or "").strip()
    if _MATH_SYMBOL_RE.search(t):
        return True
    if _CN_ARITH_RE.search(t):
        return True
    if _REASON_RE.search(t) and len(t) >= 5:
        return True
    # 两个及以上数字 + 求解疑问 + 学科语境（避免“几点、几个人去吃饭”这类闲聊误判）
    if len(_NUM_RE.findall(t)) >= 2 and _SOLVE_Q_RE.search(t) and _ACADEMIC_CTX_RE.search(t):
        return True
    return False


def pick_model(text: str, reason_mode: str, chat_model: str, reasoner_model: str):
    """返回 (模型名, 路由说明)。"""
    if reason_mode == "reasoner":
        return reasoner_model, "推理模型(强制)"
    if reason_mode == "auto" and needs_reasoning(text):
        return reasoner_model, "自动路由→推理模型"
    return chat_model, "通用模型"
