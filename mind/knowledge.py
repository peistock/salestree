"""
知识库管理
- 文档上传与解析（txt/md/pdf）
- 智能分段 + Embedding
- 语义检索
"""
import os
import re
import json
import logging
from pathlib import Path
from typing import List, Optional

from mind.embedder import Embedder
from mind.vector_store import VectorStore
from mind.memory import get_conn

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(os.getenv("DATA_DIR", "./data")) / "knowledge"
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


class KnowledgeBase:
    """销售知识库：管理所有文档和向量检索"""

    def __init__(self):
        self.embedder = Embedder()
        self.conn = get_conn()
        self.vector_store = VectorStore(self.conn)

    def ingest_file(self, file_path: str, filename: Optional[str] = None) -> dict:
        """
        摄入文档：解析 -> 分段 -> 生成 L0/L1 -> Embedding -> 存入向量库
        返回: {"doc_id": int, "chunks": int, "filename": str}
        """
        path = Path(file_path)
        if not path.exists():
            return {"error": "文件不存在"}

        fname = filename or path.name
        text = self._parse_file(path)
        if not text or len(text.strip()) < 10:
            return {"error": "无法解析文件或内容太短"}

        # 分段
        chunks = self._chunk_text(text)
        if not chunks:
            return {"error": "分段失败"}

        # 生成 L0/L1 摘要（OpenViking 启发 #1）
        chunk_metas = self._generate_chunk_meta(chunks)

        # 保存原始文档记录（metadata 包含 chunks_meta）
        meta = {
            "source": "upload",
            "chunks_meta": chunk_metas,
        }
        with self.conn.cursor() as c:
            c.execute(
                "INSERT INTO knowledge_docs (filename, content, metadata) VALUES (%s, %s, %s) RETURNING id",
                (fname, text, json.dumps(meta, ensure_ascii=False))
            )
            doc_id = c.fetchone()[0]
            self.conn.commit()

        # Embedding
        try:
            embeddings = self.embedder.encode_documents(chunks)
        except Exception as e:
            logger.error(f"Embedding 失败: {e}")
            # 回滚文档记录
            with self.conn.cursor() as c:
                c.execute("DELETE FROM knowledge_docs WHERE id = %s", (doc_id,))
                self.conn.commit()
            return {"error": f"Embedding 失败: {e}"}

        # 存入向量库
        self.vector_store.add_chunks(doc_id, chunks, embeddings)

        logger.info(f"知识库摄入完成: {fname} -> {len(chunks)} 个片段 (doc_id={doc_id})")
        return {"doc_id": doc_id, "chunks": len(chunks), "filename": fname}

    def _parse_file(self, path: Path) -> str:
        """解析文件内容"""
        suffix = path.suffix.lower()

        if suffix in (".txt", ".md"):
            return path.read_text(encoding="utf-8", errors="ignore")

        if suffix == ".pdf":
            return self._parse_pdf(path)

        # 其他格式尝试当文本读
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            logger.warning(f"无法解析文件格式: {suffix}")
            return ""

    def _parse_pdf(self, path: Path) -> str:
        """解析 PDF（简化版，优先用 PyPDF2，备选 pdfplumber）"""
        try:
            import pdfplumber
            texts = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        texts.append(text)
            return "\n".join(texts)
        except ImportError:
            logger.warning("pdfplumber 未安装，尝试 PyPDF2")
        except Exception as e:
            logger.warning(f"pdfplumber 解析失败: {e}")

        try:
            import PyPDF2
            texts = []
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        texts.append(text)
            return "\n".join(texts)
        except ImportError:
            logger.error("PyPDF2 也未安装，无法解析 PDF。请运行: pip install pdfplumber")
            return ""
        except Exception as e:
            logger.error(f"PyPDF2 解析失败: {e}")
            return ""

    def _chunk_text(self, text: str, chunk_size: int = 300, overlap: int = 50) -> List[str]:
        """
        智能分段：
        1. 先按句子分割
        2. 合并到接近 chunk_size
        3. 重叠 overlap 字符保持上下文连贯
        """
        # 按句子边界分割（中文标点）
        sentences = re.split(r'(?<=[。！？\n；])', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            #  fallback：按固定长度切
            return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size - overlap)]

        chunks = []
        current = ""

        for sent in sentences:
            if len(current) + len(sent) > chunk_size and current:
                chunks.append(current.strip())
                # 保留 overlap
                if overlap > 0 and len(current) > overlap:
                    current = current[-overlap:]
                else:
                    current = ""
            current += sent

        if current.strip():
            chunks.append(current.strip())

        return chunks

    def _generate_chunk_meta(self, chunks: List[str]) -> List[dict]:
        """为每个 chunk 生成 L0/L1 摘要（OpenViking 启发 #1）"""
        metas = []
        for chunk in chunks:
            metas.append({
                "l0": chunk[:80].replace("\n", " ") + ("..." if len(chunk) > 80 else ""),
                "l1": chunk[:250].replace("\n", " ") + ("..." if len(chunk) > 250 else ""),
            })
        return metas

    def search(self, query: str, top_k: int = 5, min_similarity: float = 0.3) -> List[dict]:
        """
        语义搜索知识库（L0 召回 -> L1 精排 -> L2 返回）
        返回: [{chunk_text, doc_id, chunk_index, similarity, l0, l1}, ...]
        """
        try:
            query_emb = self.embedder.encode_query(query)
            # 1. L0 召回：向量检索 top_k*2
            results = self.vector_store.search(query_emb, top_k=top_k * 2, min_similarity=min_similarity)
            if not results:
                return []

            # 加载各 chunk 的 metadata（L0/L1）
            doc_ids = list(set(r["doc_id"] for r in results))
            doc_meta = {}
            with self.conn.cursor() as c:
                for doc_id in doc_ids:
                    c.execute("SELECT metadata FROM knowledge_docs WHERE id=%s", (doc_id,))
                    row = c.fetchone()
                    if row and row[0]:
                        try:
                            meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                            doc_meta[doc_id] = meta.get("chunks_meta", [])
                        except Exception:
                            doc_meta[doc_id] = []

            # 附加 metadata 到结果
            for r in results:
                doc_id = r["doc_id"]
                idx = r.get("chunk_index", 0)
                meta_list = doc_meta.get(doc_id, [])
                if idx < len(meta_list):
                    r["l0"] = meta_list[idx].get("l0", "")
                    r["l1"] = meta_list[idx].get("l1", "")

            # 2. L1 精排：关键词匹配 bonus
            query_words = set(query.lower().split())
            ranked = []
            for r in results:
                score = r.get("similarity", 0)
                l1 = r.get("l1", "").lower()
                keyword_bonus = sum(0.03 for w in query_words if w in l1)
                ranked.append((r, score + keyword_bonus))

            ranked.sort(key=lambda x: x[1], reverse=True)

            # 3. 返回 top_k 个 L2 完整内容
            return [r[0] for r in ranked[:top_k]]
        except Exception as e:
            logger.error(f"知识库搜索失败: {e}")
            return []

    def list_docs(self) -> List[dict]:
        """列出所有知识库文档"""
        with self.conn.cursor() as c:
            c.execute(
                "SELECT id, filename, created_at FROM knowledge_docs ORDER BY created_at DESC"
            )
            return [{"id": r[0], "filename": r[1], "created_at": r[2]} for r in c.fetchall()]

    def delete_doc(self, doc_id: int) -> bool:
        """删除文档及其向量"""
        try:
            self.vector_store.delete_by_doc(doc_id)
            with self.conn.cursor() as c:
                c.execute("DELETE FROM knowledge_docs WHERE id = %s", (doc_id,))
                self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return False

    def get_stats(self) -> dict:
        """知识库统计"""
        with self.conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM knowledge_docs")
            doc_count = c.fetchone()[0]
        vector_count = self.vector_store.count()
        return {"documents": doc_count, "vector_chunks": vector_count}

    def ingest_bytes(self, content: bytes, filename: str) -> dict:
        """
        从内存字节摄入文档（供 dashboard 文件上传调用）
        返回: {"doc_id": int, "chunks": int, "filename": str}
        """
        import tempfile
        suffix = Path(filename).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode='wb') as f:
            f.write(content)
            tmp_path = f.name
        try:
            result = self.ingest_file(tmp_path, filename=filename)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        return result

    def close(self):
        self.conn.close()
