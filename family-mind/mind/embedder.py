"""
本地 Embedding 模型封装
- 默认模型: BAAI/bge-small-zh-v1.5 (512维, 中文优化)
- 支持 sentence-transformers 本地加载，或外部 OpenAI 兼容 embeddings API（如 LM Studio）
- 首次本地运行会自动下载模型 (~100MB)
- 查询时使用 BGE 推荐的前缀优化召回率
"""
import os
import logging
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
DIMENSION = 512

# OpenAI 兼容 embeddings API 配置（例如 LM Studio 的 /v1/embeddings）
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "").strip()
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "lm-studio")
EMBEDDING_API_MODEL = os.getenv("EMBEDDING_API_MODEL", MODEL_NAME)


class Embedder:
    """单例模式，避免重复加载模型"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _load(self):
        if self._initialized:
            return
        # API 模式：不加载本地模型，直接调用外部 embeddings 服务
        if EMBEDDING_BASE_URL:
            self._api_mode = True
            self._initialized = True
            logger.info(f"使用外部 embeddings API: {EMBEDDING_BASE_URL}")
            return

        self._api_mode = False
        logger.info(f"加载 Embedding 模型: {MODEL_NAME}")
        try:
            local_only = os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "true").lower() in ("1", "true", "yes")
            self._model = SentenceTransformer(MODEL_NAME, local_files_only=local_only)
            self._initialized = True
            logger.info("Embedding 模型加载完成")
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            self._load_failed = True

    def encode(self, texts: list) -> np.ndarray:
        """批量编码文本为向量"""
        self._load()
        if getattr(self, '_load_failed', False):
            raise RuntimeError("Embedding 模型未加载")
        if isinstance(texts, str):
            texts = [texts]

        if getattr(self, '_api_mode', False):
            return self._encode_api(texts)

        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False
        )
        return embeddings

    def _encode_api(self, texts: list) -> np.ndarray:
        """调用 OpenAI 兼容 embeddings API"""
        import requests
        url = EMBEDDING_BASE_URL.rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {EMBEDDING_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": EMBEDDING_API_MODEL,
            "input": texts,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60, proxies={"http": None, "https": None})
            resp.raise_for_status()
            data = resp.json()
            embs = [item["embedding"] for item in data["data"]]
            arr = np.array(embs, dtype=np.float32)
            # 归一化，保持与本地模型一致
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1
            return arr / norms
        except Exception as e:
            logger.error(f"Embeddings API 调用失败: {e}")
            raise RuntimeError(f"Embeddings API 调用失败: {e}")

    def encode_query(self, query: str) -> np.ndarray:
        """编码查询（使用 BGE 推荐的前缀提升检索效果）"""
        # BGE 官方推荐查询前缀
        prefixed = f"为这个句子生成表示以用于检索相关文章：{query}"
        return self.encode([prefixed])[0]

    def encode_documents(self, texts: list) -> np.ndarray:
        """编码文档（不加前缀）"""
        return self.encode(texts)
