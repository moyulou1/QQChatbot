"""
人格配置加载器
负责读取 YAML 人格文件，并将其转换为 LLM 可用的系统提示词
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


class PersonalityConfig(BaseModel):
    """人格配置数据模型"""
    basic: dict[str, Any] = Field(default_factory=dict)
    personality_traits: list[str] = Field(default_factory=list)
    speech_style: dict[str, Any] = Field(default_factory=dict)
    background_story: str = ""
    knowledge: dict[str, Any] = Field(default_factory=dict)
    reply_behavior: dict[str, Any] = Field(default_factory=dict)
    social: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    # 输出后处理（双保险，防止模型偶发蹦出现代表述）
    output_filter: dict[str, Any] = Field(default_factory=dict)

    # 原始YAML内容，用于调试
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


# emoji / 常见符号表情正则（保留中文标点）
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U0001F018-\U0001F270"
    "]+",
    flags=re.UNICODE,
)


PersonalityConfig.model_rebuild()


class PersonalityLoader:
    """人格加载器"""

    def __init__(self, personality_dir: str, personality_file: str = "default"):
        self.personality_dir = Path(personality_dir)
        self.personality_file = personality_file
        self._config: Optional[PersonalityConfig] = None

    def load(self) -> PersonalityConfig:
        """加载人格配置文件"""
        file_path = self.personality_dir / f"{self.personality_file}.yaml"
        if not file_path.exists():
            file_path = self.personality_dir / f"{self.personality_file}.yml"

        if not file_path.exists():
            raise FileNotFoundError(
                f"人格文件未找到: {file_path}\n"
                f"请确认 PERSONALITY_FILE 配置正确，或在 {self.personality_dir} 下创建对应文件"
            )

        with open(file_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)

        if not isinstance(raw_data, dict):
            raise ValueError(f"人格文件格式错误: {file_path}")

        config = PersonalityConfig(raw=raw_data, **raw_data)
        self._config = config
        return config

    @property
    def config(self) -> PersonalityConfig:
        """获取已加载的配置（懒加载）"""
        if self._config is None:
            self.load()
        return self._config

    def reload(self) -> PersonalityConfig:
        """重新加载人格配置（运行时切换人格用）"""
        self._config = None
        return self.load()

    def build_system_prompt(self, group_name: str = "", group_id: str = "") -> str:
        """
        根据人格配置构建系统提示词
        这是发送给 LLM 的核心人格指令
        """
        cfg = self.config
        lines: list[str] = []

        # ===== 角色设定 =====
        lines.append("# 角色设定")
        name = cfg.basic.get("name", "助手")
        gender = cfg.basic.get("gender", "")
        age = cfg.basic.get("age", "")
        age_str = f"{age}岁" if age else ""
        gender_str = gender if gender else ""
        lines.append(f"你是{name}，一个{age_str}{gender_str}。")

        if cfg.background_story:
            lines.append(f"\n## 背景故事\n{cfg.background_story.strip()}")

        # ===== 性格特质 =====
        if cfg.personality_traits:
            lines.append(f"\n## 性格特质\n你的性格特点是：{'、'.join(cfg.personality_traits)}。")
            lines.append("你的所有言行都必须符合这些性格特质，不能出现与设定矛盾的表现。")

        # ===== 说话风格 =====
        lines.append("\n## 说话风格")
        style = cfg.speech_style
        if style.get("tone"):
            lines.append(f"- 整体语气：{style['tone']}")
        if style.get("sentence_length"):
            lines.append(f"- 句式偏好：{style['sentence_length']}")

        if style.get("use_emoji"):
            freq = style.get("emoji_frequency", "适中")
            emojis = style.get("common_emojis", [])
            lines.append(f"- 可以使用表情符号，使用频率：{freq}")
            if emojis:
                lines.append(f"- 常用表情：{' '.join(emojis)}")

        if style.get("catchphrases"):
            lines.append(f"- 口头禅（偶尔自然使用，不要每句都带）：{'；'.join(style['catchphrases'])}")

        if style.get("avoid_words"):
            lines.append(f"- 绝对禁止使用的表述：{'、'.join(style['avoid_words'])}")
            lines.append("  不要暴露自己是AI或程序，始终以角色身份对话。")

        # ===== 知识与能力 =====
        lines.append("\n## 知识与能力")
        knowledge = cfg.knowledge
        if knowledge.get("expertise"):
            lines.append(f"- 你擅长：{'、'.join(knowledge['expertise'])}")
        if knowledge.get("limitations"):
            lines.append(f"- {knowledge['limitations'].strip()}")
        if knowledge.get("opinion_on"):
            lines.append("- 你对一些事物有固定看法：")
            for topic, opinion in knowledge["opinion_on"].items():
                lines.append(f"  * {topic}：{opinion}")

        # ===== 回复行为 =====
        lines.append("\n## 回复行为规范")
        behavior = cfg.reply_behavior
        if behavior.get("default_length"):
            lines.append(f"- 回复长度：{behavior['default_length']}，不要长篇大论。")
        if behavior.get("max_length"):
            lines.append(f"- 单条回复最多{behavior['max_length']}字。")
        if behavior.get("ask_clarification"):
            lines.append("- 当对方的问题模糊不清时，主动追问确认，不要瞎猜。")
        if behavior.get("rejection_style"):
            lines.append(f"- 拒绝不当请求时的风格：{behavior['rejection_style']}。")

        # ===== 群聊社交 =====
        lines.append("\n## 群聊社交规则")
        social = cfg.social
        if social.get("relationship_with_group"):
            lines.append(f"- 你与群的关系：{social['relationship_with_group']}")
        if social.get("how_to_address_others"):
            lines.append(f"- 称呼他人：{social['how_to_address_others']}")
        if social.get("group_dynamics"):
            lines.append(f"- 你的群聊定位：{social['group_dynamics']}")
        lines.append("- 这是群聊场景，回复要自然融入对话，不要像私聊一样长篇大论。")
        lines.append("- 关注当前对话上下文，回应正在讨论的话题，不要突然跳转。")

        if group_name:
            lines.append(f"- 当前群名：{group_name}")

        # ===== 安全规则 =====
        lines.append("\n## 安全规则")
        safety = cfg.safety
        safe_template = safety.get("safe_response_template", "这个话题不太方便聊呢~")
        rules = []
        if safety.get("refuse_political"):
            rules.append("敏感政治话题")
        if safety.get("refuse_violence"):
            rules.append("暴力、血腥、自残相关内容")
        if safety.get("refuse_personal_info"):
            rules.append("索要他人隐私信息")
        if safety.get("refuse_harmful"):
            rules.append("违法犯罪或有害行为的指导")
        if rules:
            lines.append(f"- 遇到以下情况时，用「{safe_template}」委婉拒绝并转移话题：")
            for r in rules:
                lines.append(f"  * {r}")

        # ===== 输出格式 =====
        lines.append("\n## 输出要求")
        lines.append("- 直接输出你要说的话，不要加任何前缀、解释或角色扮演标记。")
        lines.append("- 不要用「小助手：」「我：」等前缀，直接输出对话内容。")
        lines.append("- 不要输出 markdown 格式，用纯文本回复。")

        return "\n".join(lines)

    def get_name(self) -> str:
        """获取人格名字"""
        return self.config.basic.get("name", "助手")

    def get_safe_template(self) -> str:
        """获取安全拒绝模板"""
        return self.config.safety.get("safe_response_template", "这个话题不太方便聊呢~")

    def apply_output_filter(self, text: str) -> str:
        """
        按人格 output_filter 配置对模型输出做最后一道清洗（双保险）：
        - strip_emoji: 剔除 emoji 与符号表情
        - replacements: 现代白话 -> 古白话 的词替换（先执行）
        - removals: 直接删除的现代语气词/违禁词（后执行）
        未配置 output_filter 时原样返回，不影响其他人格。
        """
        if not text:
            return text
        f = self.config.output_filter
        if not f:
            return text

        out = text
        for bad, good in (f.get("replacements") or {}).items():
            if bad:
                out = out.replace(bad, good)
        for bad in (f.get("removals") or []):
            if bad:
                out =out.replace(bad, "")
        # 剥离括号包裹的动作/神态/心理旁白，只留说出口的话（跑两遍防一层嵌套）
        if f.get("strip_parentheses"):
            for _ in range(2):
                out = re.sub(r"（[^（）]*）", "", out)
                out = re.sub(r"\([^()]*\)", "", out)
        if f.get("strip_emoji"):
            out = _EMOJI_RE.sub("", out)

        # 清洗后可能留下多余空白与错位标点，做轻度收敛（保留中文正常标点）
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = out.replace("~", "").replace("～", "")
        # 删词/删旁白后可能句首残留空白、逗号、顿号
        out = re.sub(r"^\s*[，、\s]+", "", out)
        # 删词后可能出现 "？，" "。，" 这类叠标点，保留句末标点、去掉多余的逗号顿号
        out = re.sub(r"([。！？])[，、]+", r"\1", out)
        out = re.sub(r"([，。！？；：、])\1+", r"\1", out)
        # 句末删词后可能残留逗号/顿号（如"我晓得了，"），去掉
        out = re.sub(r"[，、]\s*$", "", out)
        return out.strip()

    def preview_from_content(self, yaml_content: str, group_name: str = "") -> str:
        """
        基于任意 YAML 内容生成系统提示词预览（不修改当前加载的人格）
        用于 Web 控制台的实时预览功能
        """
        import yaml as _yaml
        data = _yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            raise ValueError("YAML 格式错误：根节点必须是字典")
        temp_config = PersonalityConfig(raw=data, **data)
        old_config = self._config
        self._config = temp_config
        try:
            prompt = self.build_system_prompt(group_name=group_name)
        finally:
            self._config = old_config
        return prompt
