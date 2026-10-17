"""LLM 工厂：OpenAI 兼容接口（默认 DeepSeek，可换 Qwen/智谱/硅基流动）。"""
from langchain_openai import ChatOpenAI

import config


def get_llm(temperature: float = 0.2) -> ChatOpenAI:
    """创建聊天模型实例。未配置 key 时返回占位，调用时由服务层给出友好报错。"""
    return ChatOpenAI(
        model=config.LLM_MODEL,
        api_key=config.LLM_API_KEY or "sk-none",
        base_url=config.LLM_BASE_URL,
        temperature=temperature,
        timeout=60,
    )
