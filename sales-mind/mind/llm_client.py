"""
国内大模型通用适配层
- 支持所有 OpenAI-compatible API（DeepSeek、Kimi、通义、智谱、字节、Agnes 等）
- 统一封装 chat_completion，屏蔽各家差异
- 自动处理 JSON 模式、重试、指数退避、跨 provider 故障转移

Hermes error_classifier.py 的精简适配：
- 将错误分类为 RETRY / BACKOFF / FAILOVER / FATAL
-  provider 耗尽时按错误类型决定立刻重试、退避重试或切换 provider
- 通过环境变量配置多个 fallback provider，本地 LM Studio 不可用时自动切云端
"""
import os
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Callable

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


class FailoverDecision(Enum):
    """Hermes 风格的错误处理决策"""
    RETRY = "retry"           # 同 provider 立即重试
    BACKOFF = "backoff"       # 同 provider 指数退避重试
    FAILOVER = "failover"     # 切换下一个 provider
    FATAL = "fatal"           # 不再重试，返回空结果


@dataclass
class ProviderConfig:
    """一个 LLM provider 的接入配置"""
    name: str
    base_url: str
    api_key: str
    model_daily: str
    model_complex: str
    model_summary: str

    def client(self, timeout: int = 300) -> OpenAI:
        return OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=timeout)


class ProviderFailover(Exception):
    """内部信号：当前 provider 需要被跳过，尝试下一个 provider"""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def classify_api_error(e: Exception) -> tuple[FailoverDecision, str]:
    """
    对 LLM API 异常进行分类，返回 (decision, reason)。
    改编自 Hermes agent/error_classifier.py 的核心思想。
    """
    # openai SDK 未安装时，这些类型都是 Exception，isinstance 会误判
    if OpenAI is None:
        return FailoverDecision.FATAL, f"no_openai_sdk:{type(e).__name__}"

    msg = str(e).lower()

    # 1. 超时 / 限流 → 退避重试（优先用类型判断，再用消息兜底）
    if isinstance(e, APITimeoutError) or "timeout" in msg:
        return FailoverDecision.BACKOFF, "timeout"
    if isinstance(e, RateLimitError) or any(k in msg for k in ("rate limit", "ratelimit", "too many requests")):
        return FailoverDecision.BACKOFF, "rate_limit"

    # 2. 连接层错误 → 切换 provider（可能是本地服务挂了 / 网络不通）
    e_type = type(e).__name__.lower()
    if any(k in e_type for k in ("connection", "connecterror", "ssl", "certificate", "newconnection")):
        return FailoverDecision.FAILOVER, f"connection_error:{e_type}"

    # 3. HTTP 状态码分类
    if isinstance(e, APIStatusError):
        code = getattr(e, "status_code", None)
        if code in (408, 429):
            return FailoverDecision.BACKOFF, f"status_{code}"
        if code in (500, 502, 503, 504):
            return FailoverDecision.BACKOFF, f"status_{code}"
        if code in (401, 403):
            return FailoverDecision.FAILOVER, f"auth_error:{code}"
        if code == 404:
            # 路由找不到：可能是 provider 上游映射问题，换 provider 试试
            return FailoverDecision.FAILOVER, "status_404"

    # 4. 根据错误消息识别上下文溢出、密钥等特定场景
    if any(k in msg for k in ("context length", "too long", "maximum context", "max tokens")):
        # 上下文太长：换 provider 通常无法解决，但云端模型上下文更大，可尝试
        return FailoverDecision.FAILOVER, "context_overflow"
    if any(k in msg for k in ("invalid api key", "authentication", "unauthorized", "api key")):
        return FailoverDecision.FAILOVER, "invalid_key"

    # 5. 默认：未知异常不再重试，避免无限循环
    return FailoverDecision.FATAL, f"unknown:{type(e).__name__}"


class LLMClient:
    """通用 LLM 客户端，支持多 provider 故障转移"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _init(self):
        if OpenAI is None:
            raise ImportError("请安装 openai SDK: pip install openai>=1.0.0")

        self.providers = self._build_providers()
        if not self.providers:
            raise RuntimeError("未配置任何 LLM provider")

        primary = self.providers[0]
        # 保留旧属性，确保调用方无需修改
        self.client = primary.client()
        self.model_daily = primary.model_daily
        self.model_complex = primary.model_complex
        self.model_summary = primary.model_summary
        self.reasoning_effort = os.getenv("REASONING_EFFORT", "").strip() or None
        self._initialized = True

        logger.info(
            f"LLM 客户端初始化: providers={[p.name for p in self.providers]}, "
            f"daily={self.model_daily}, complex={self.model_complex}"
        )

    def _build_providers(self) -> List[ProviderConfig]:
        """构建 provider 列表：primary + 可选 fallback"""
        providers: List[ProviderConfig] = []

        # primary
        primary = ProviderConfig(
            name="primary",
            base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.getenv("LLM_API_KEY", DEFAULT_API_KEY),
            model_daily=os.getenv("MODEL_DAILY", DEFAULT_MODEL_DAILY),
            model_complex=os.getenv("MODEL_COMPLEX", DEFAULT_MODEL_COMPLEX),
            model_summary=os.getenv("MODEL_SUMMARY", DEFAULT_MODEL_SUMMARY),
        )
        providers.append(primary)

        # fallbacks: 通过 LLM_FALLBACK_* 环境变量配置
        urls = self._split_env("LLM_FALLBACK_URLS")
        keys = self._split_env("LLM_FALLBACK_KEYS")
        names = self._split_env("LLM_FALLBACK_NAMES")
        dailies = self._split_env("LLM_FALLBACK_DAILY")
        complexes = self._split_env("LLM_FALLBACK_COMPLEX")
        summaries = self._split_env("LLM_FALLBACK_SUMMARY")

        n = max(len(urls), len(keys))
        for i in range(n):
            url = urls[i] if i < len(urls) else primary.base_url
            key = keys[i] if i < len(keys) else primary.api_key
            name = names[i] if i < len(names) else f"fallback_{i + 1}"
            if not url or not key:
                logger.warning(f"Fallback provider {i + 1} 配置不完整，跳过")
                continue
            providers.append(ProviderConfig(
                name=name,
                base_url=url,
                api_key=key,
                model_daily=dailies[i] if i < len(dailies) else primary.model_daily,
                model_complex=complexes[i] if i < len(complexes) else primary.model_complex,
                model_summary=summaries[i] if i < len(summaries) else primary.model_summary,
            ))

        return providers

    @staticmethod
    def _split_env(name: str) -> List[str]:
        """读取逗号分隔的环境变量，去除空白和空值"""
        raw = os.getenv(name, "")
        if not raw:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

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

    def _resolve_model(self, provider: ProviderConfig, role: str, override: Optional[str]) -> str:
        """根据 role 和显式 model 参数，解析当前 provider 应使用的模型名"""
        if override:
            return override
        return getattr(provider, f"model_{role}", None) or provider.model_daily

    def _execute_on_provider(
        self,
        provider: ProviderConfig,
        model: str,
        label: str,
        fn: Callable[[OpenAI, str], any],
    ) -> any:
        """
        在单个 provider 上执行 fn，支持错误分类、立即重试、指数退避。
        需要切换 provider 时抛出 ProviderFailover。
        """
        client = provider.client()
        last_error = None

        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            try:
                return fn(client, model)
            except Exception as e:
                last_error = e
                self._log_api_error(label, model, e)
                decision, reason = classify_api_error(e)

                if decision == FailoverDecision.FATAL:
                    raise last_error

                if decision == FailoverDecision.FAILOVER:
                    raise ProviderFailover(reason) from e

                if attempt < _RETRY_MAX_ATTEMPTS:
                    delay = (
                        _RETRY_DELAY_SECONDS
                        if decision == FailoverDecision.RETRY
                        else _RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                    )
                    logger.info(
                        f"LLM {label} provider={provider.name} 遇到 {reason}，"
                        f"{delay}s 后退避重试 (attempt {attempt}/{_RETRY_MAX_ATTEMPTS})"
                    )
                    time.sleep(delay)
                    continue

        # 同 provider 重试耗尽，抛 failover 让外层尝试下一个 provider
        raise ProviderFailover(f"exhausted_retries:{last_error}") from last_error

    def _execute_with_failover(
        self,
        role: str,
        model: Optional[str],
        label: str,
        fn: Callable[[OpenAI, str], any],
    ) -> Optional[any]:
        """按 provider 优先级执行，直到成功或全部失败"""
        self._init()

        if not self.providers:
            logger.error(f"[{label}] 无可用 provider")
            return None

        for provider in self.providers:
            resolved_model = self._resolve_model(provider, role, model)
            try:
                return self._execute_on_provider(provider, resolved_model, label, fn)
            except ProviderFailover as e:
                logger.warning(f"[{label}] provider={provider.name} 需要切换: {e.reason}")
                continue
            except Exception as e:
                logger.error(f"[{label}] provider={provider.name} 最终失败: {e}")
                continue

        logger.error(f"[{label}] 所有 provider 均已耗尽")
        return None

    def chat(self, system: str, user_prompt: str, model: str = None, max_tokens: int = 1500, temperature: float = 0.7, json_mode: bool = False, image_path: str = None) -> str:
        """
        统一对话接口
        - system: 系统提示词
        - user_prompt: 用户输入
        - model: 指定模型，None 则用 daily 模型
        - json_mode: 是否强制输出 JSON
        - image_path: 本地图片路径，支持 vision 分析
        """
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

        def _fn(client: OpenAI, resolved_model: str):
            kwargs = {
                "model": resolved_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if self.reasoning_effort:
                kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}
            r = client.chat.completions.create(**kwargs)
            return (r.choices[0].message.content or "").strip()

        result = self._execute_with_failover("daily", model, "chat", _fn)
        return result or ""

    def chat_with_tools(self, messages: list, tools: list = None,
                        model: str = None, max_tokens: int = 4096,
                        temperature: float = 0.7) -> Optional[dict]:
        """
        Tool Calling 接口：传入消息历史（含 tool results），返回 LLM 响应
        响应包含 .choices[0].message.content 或 .choices[0].message.tool_calls
        """
        def _fn(client: OpenAI, resolved_model: str):
            kwargs = {
                "model": resolved_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if self.reasoning_effort:
                kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}
            return client.chat.completions.create(**kwargs)

        return self._execute_with_failover("complex", model, "chat_with_tools", _fn)

    def _fallback_model(self, current: str) -> Optional[str]:
        """保留旧接口：返回一个与当前模型不同的 fallback 模型名。"""
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
