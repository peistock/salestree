"""
PGVector 向量数据库封装
- 存储知识库文本片段的向量
- 支持余弦相似度搜索
- 与 PostgreSQL 共用连接
"""
import logging
import numpy as np
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, conn):
        self.conn = conn

    def add_chunks(self, doc_id: int, chunks: list, embeddings: np.ndarray):
        """批量添加文本片段和向量"""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks 和 embeddings 长度不一致")

        with self.conn.cursor() as c:
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                emb_list = emb.tolist() if hasattr(emb, 'tolist') else list(emb)
                c.execute(
                    """
                    INSERT INTO knowledge_embeddings (doc_id, chunk_index, chunk_text, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    (doc_id, i, chunk, emb_list)
                )
            self.conn.commit()
        logger.info(f"向量库新增 {len(chunks)} 个片段 (doc_id={doc_id})")

    def search(self, query_embedding: np.ndarray, top_k: int = 5, min_similarity: float = 0.3) -> list:
        """
        向量相似度搜索
        返回: [{chunk_text, doc_id, chunk_index, similarity}, ...]
        """
        emb_list = query_embedding.tolist() if hasattr(query_embedding, 'tolist') else list(query_embedding)

        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT chunk_text, doc_id, chunk_index,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM knowledge_embeddings
                WHERE 1 - (embedding <=> %s::vector) >= %s
                ORDER BY similarity DESC
                LIMIT %s
                """,
                (emb_list, emb_list, min_similarity, top_k)
            )
            results = c.fetchall()

        logger.debug(f"向量检索: top_k={top_k}, 命中 {len(results)} 条")
        return [dict(r) for r in results]

    def delete_by_doc(self, doc_id: int):
        """删除某个文档的所有向量片段"""
        with self.conn.cursor() as c:
            c.execute("DELETE FROM knowledge_embeddings WHERE doc_id = %s", (doc_id,))
            self.conn.commit()
        logger.info(f"向量库删除 doc_id={doc_id}")

    def count(self) -> int:
        """统计向量库中的片段总数"""
        with self.conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM knowledge_embeddings")
            return c.fetchone()[0]
