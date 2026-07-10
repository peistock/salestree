"""
国内大模型通用适配层
- 支持所有 OpenAI-compatible API（DeepSeek、Kimi、通义、智谱、字节等）
- 统一封装 chat_completion，屏蔽各家差异
- 自动处理 JSON 模式、重试、降级
"""
import os
import base64
import json
import logging
from typing import List, Dict, Optional

try:
    from openai import OpenAI
    from openai import APIError, APIStatusError, APITimeoutError, RateLimitError
except ImportError:
    OpenAI = None
    APIError = APIStatusError = APITimeoutError = RateLimitError = Exception

logger = logging.getLogger(__name__)

# 可重试状态码 / 异常类型
# 404 加入重试：Agnes API 偶发上游路由 NotFound，实际模型存在
_RETRY_STATUS_CODES = {404, 408, 429, 500, 502, 503, 504}
_RETRY_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 1.5

# 默认配置：LM Studio 本地模型
DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_API_KEY = "lm-studio"
DEFAULT_MODEL_DAILY = "qwen/qwen3.6-35b-a3b"
DEFAULT_MODEL_COMPLEX = "qwen/qwen3.6-35b-a3b"
DEFAULT_MODEL_SUMMARY = "qwen/qwen3.6-35b-a3b"


class LLMClient:
    """通用 LLM 客户端"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _init(self):
        if OpenAI is None:
            raise ImportError("请安装 openai SDK: pip install openai>=1.0.0")

        base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
        api_key = os.getenv("LLM_API_KEY", DEFAULT_API_KEY)

        if not api_key:
            logger.warning("LLM_API_KEY 未设置，模型调用将失败")

        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=300)
        self.model_daily = os.getenv("MODEL_DAILY", DEFAULT_MODEL_DAILY)
        self.model_complex = os.getenv("MODEL_COMPLEX", DEFAULT_MODEL_COMPLEX)
        self.model_summary = os.getenv("MODEL_SUMMARY", DEFAULT_MODEL_SUMMARY)
        self.reasoning_effort = os.getenv("REASONING_EFFORT", "").strip() or None
        self._initialized = True

        logger.info(f"LLM 客户端初始化: base_url={base_url}, daily={self.model_daily}, complex={self.model_complex}")

    @staticmethod
    def _log_api_error(method: str, model: str, e: Exception):
        """统一记录 LLM API 异常，包含状态码、响应体、request_id 等诊断信息。"""
        base_msg = f"LLM API 异常 [{method}] model={model}: {e}"
        extras = {}
        try:
            if isinstance(e, APIStatusError):
                extras["status_code"] = getattr(e, "status_code", None)
                extras["response_body"] = getattr(e, "response", None) and getattr(e.response, "text", None)
                extras["request_id"] = getattr(e, "request_id", None) or (
                    getattr(e, "response", None) and getattr(e.response, "headers", None) and
                    getattr(e.response.headers, "get", lambda k: None)("x-request-id")
                )
                extras["url"] = getattr(e, "request", None) and getattr(e.request, "url", None)
            else:
                extras["exception_type"] = type(e).__name__
        except Exception:
            pass
        if extras:
            logger.error(f"{base_msg} | details={extras}")
        else:
            logger.error(base_msg)

    @staticmethod
    def _should_retry(e: Exception) -> bool:
        """判断异常是否值得重试一次。"""
        if isinstance(e, (APITimeoutError, RateLimitError)):
            return True
        if isinstance(e, APIStatusError):
            code = getattr(e, "status_code", None)
            if code in _RETRY_STATUS_CODES:
                return True
        return False

    def _call_with_retry(self, fn, model: str, label: str = ""):
        """执行一次 LLM 调用，遇到可重试错误时等待后重试一次。"""
        last_error = None
        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            try:
                return fn()
            except Exception as e:
                last_error = e
                self._log_api_error(label, model, e)
                if attempt < _RETRY_MAX_ATTEMPTS and self._should_retry(e):
                    logger.info(f"LLM {label} 遇到可重试错误，{_RETRY_DELAY_SECONDS}s 后重试 (attempt {attempt}/{_RETRY_MAX_ATTEMPTS})")
                    import time
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                break
        raise last_error

    def chat(self, system: str, user_prompt: str, model: str = None, max_tokens: int = 1500, temperature: float = 0.7, json_mode: bool = False, image_path: str = None) -> str:
        """
        统一对话接口
        - system: 系统提示词
        - user_prompt: 用户输入
        - model: 指定模型，None 则用 daily 模型
        - json_mode: 是否强制输出 JSON
        - image_path: 本地图片路径，支持 vision 分析
        """
        self._init()

        if not self.client.api_key:
            return ""

        model = model or self.model_daily

        messages = [{"role": "system", "content": system}]

        if image_path and os.path.exists(image_path):
            try:
                ext = os.path.splitext(image_path)[1].lower().replace(".", "")
                if ext in ("jpg", "jpeg"):
                    mime = "image/jpeg"
                elif ext == "png":
                    mime = "image/png"
                elif ext == "gif":
                    mime = "image/gif"
                else:
                    mime = "image/jpeg"
                with open(image_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ]
                })
            except Exception as e:
                logger.warning(f"读取图片失败: {e}")
                messages.append({"role": "user", "content": user_prompt})
        else:
            messages.append({"role": "user", "content": user_prompt})

        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            # JSON 模式（各家支持情况不同，优先用 prompt 约束）
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            # DeepSeek v4 thinking mode
            if self.reasoning_effort:
                kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}

            def _do_chat():
                r = self.client.chat.completions.create(**kwargs)
                return (r.choices[0].message.content or "").strip()

            return self._call_with_retry(_do_chat, model, "chat")

        except Exception as e:
            self._log_api_error("chat", model, e)
            # 降级：切到与当前模型不同的另一个模型重试一次
            fallback = self._fallback_model(model)
            if fallback:
                logger.info(f"降级到 {fallback} 重试...")
                try:
                    kwargs["model"] = fallback

                    def _do_chat_fallback():
                        r = self.client.chat.completions.create(**kwargs)
                        return (r.choices[0].message.content or "").strip()

                    return self._call_with_retry(_do_chat_fallback, fallback, "chat_fallback")
                except Exception as e2:
                    self._log_api_error("chat_fallback", fallback, e2)
            return ""

    def chat_with_tools(self, messages: list, tools: list = None,
                        model: str = None, max_tokens: int = 4096,
                        temperature: float = 0.7) -> Optional[dict]:
        """
        Tool Calling 接口：传入消息历史（含 tool results），返回 LLM 响应
        响应包含 .choices[0].message.content 或 .choices[0].message.tool_calls
        """
        self._init()

        if not self.client.api_key:
            return None

        model = model or self.model_complex

        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            # DeepSeek v4 thinking mode
            if self.reasoning_effort:
                kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}

            def _do_chat_with_tools():
                return self.client.chat.completions.create(**kwargs)

            return self._call_with_retry(_do_chat_with_tools, model, "chat_with_tools")
        except Exception as e:
            self._log_api_error("chat_with_tools", model, e)
            # 降级：切到与当前模型不同的另一个模型重试一次
            fallback = self._fallback_model(model)
            if fallback:
                logger.info(f"[chat_with_tools] 降级到 {fallback} 重试...")
                try:
                    kwargs["model"] = fallback

                    def _do_tools_fallback():
                        return self.client.chat.completions.create(**kwargs)

                    return self._call_with_retry(_do_tools_fallback, fallback, "chat_with_tools_fallback")
                except Exception as e2:
                    self._log_api_error("chat_with_tools_fallback", fallback, e2)
            return None

    def _fallback_model(self, current: str) -> Optional[str]:
        """返回一个与当前模型不同的 fallback 模型名；无可用差异模型则返回 None。"""
        candidates = [self.model_daily, self.model_complex, self.model_summary]
        for c in candidates:
            if c and c != current:
                return c
        return None

    def is_ready(self) -> bool:
        """检查客户端是否可用"""
        try:
            self._init()
            return self.client is not None
        except:
            return False


# 全局快捷函数
def chat(system: str, user_prompt: str, model: str = None, **kwargs) -> str:
    """快捷调用"""
    return LLMClient().chat(system, user_prompt, model, **kwargs)

def chat_with_tools(messages: list, tools: list = None, model: str = None, **kwargs):
    """Tool Calling 快捷调用"""
    return LLMClient().chat_with_tools(messages, tools, model, **kwargs)
