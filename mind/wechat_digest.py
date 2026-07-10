"""
微信公众号 · 墨摘知识库桥接层

把 wechat-digest-skill 产出的 knowledge_base.json 同步进销销的 PGVector 知识库，
让销销可以基于公众号/行业资讯做营销方案、客户分享、销售触达。

依赖：
- third_party/wechat-digest-skill/output/knowledge_base.json（默认）
- 环境变量 WECHAT_DIGEST_KB_PATH 可覆盖路径
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from mind.knowledge import KnowledgeBase
from mind.memory import get_conn

logger = logging.getLogger(__name__)

DEFAULT_KB_PATH = (
    Path(__file__).parent.parent
    / "third_party"
    / "wechat-digest-skill"
    / "output"
    / "knowledge_base.json"
)
KB_PATH = Path(os.getenv("WECHAT_DIGEST_KB_PATH", DEFAULT_KB_PATH))

DOC_PREFIX = "wechat_digest:"


def _load_kb(path: Optional[Path] = None) -> Dict:
    p = Path(path or KB_PATH)
    if not p.exists():
        logger.warning(f"wechat-digest 知识库不存在: {p}")
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取 wechat-digest 知识库失败: {e}")
        return {}


def _doc_key(article_id: str) -> str:
    return f"{DOC_PREFIX}{article_id}"


def _article_text(article: Dict, topics: List[str]) -> str:
    """把一篇文章整理成适合向量化的大段文本。"""
    parts = []
    if article.get("title"):
        parts.append(f"标题：{article['title']}")
    if article.get("account"):
        parts.append(f"公众号：{article['account']}")
    if topics:
        parts.append(f"主题：{', '.join(topics)}")
    if article.get("publishDate"):
        parts.append(f"发布时间：{article['publishDate']}")

    analysis = article.get("analysis") or {}
    if analysis.get("summary"):
        parts.append(f"\n一句话总结：{analysis['summary']}")
    if analysis.get("viewpoints"):
        parts.append("\n核心观点：\n" + "\n".join(f"- {v}" for v in analysis["viewpoints"]))
    if analysis.get("data"):
        parts.append("\n关键数据：\n" + "\n".join(f"- {d}" for d in analysis["data"]))
    if analysis.get("tags"):
        parts.append(f"\n标签：{', '.join(analysis['tags'])}")
    if analysis.get("audience"):
        parts.append(f"\n适用人群：{analysis['audience']}")

    if article.get("digest"):
        parts.append(f"\n摘要：{article['digest']}")
    if article.get("content"):
        parts.append(f"\n正文：{article['content']}")

    return "\n".join(parts)


def _topic_names_for_article(article_id: str, kb: Dict) -> List[str]:
    names = []
    for t in kb.get("topics", []):
        if article_id in t.get("articleIds", []):
            names.append(t.get("name") or t.get("id", ""))
    return names


def sync(kb_path: Optional[str] = None, dry_run: bool = False) -> Dict:
    """
    将 wechat-digest 的 knowledge_base.json 同步进销销 PGVector 知识库。
    以文章 id 为稳定键去重，已存在则全量更新。

    返回：{"total": 总数, "added": 新增, "updated": 更新, "skipped": 跳过, "errors": 失败}
    """
    data = _load_kb(kb_path)
    articles = data.get("articles", {})
    if not articles:
        return {"error": "wechat-digest 知识库为空或不存在"}

    kb = KnowledgeBase()
    stats = {"total": len(articles), "added": 0, "updated": 0, "skipped": 0, "errors": 0}

    try:
        for aid, article in articles.items():
            try:
                text = _article_text(article, _topic_names_for_article(aid, data))
                if not text or len(text.strip()) < 20:
                    logger.warning(f"文章 {aid} 内容过短，跳过")
                    stats["skipped"] += 1
                    continue

                chunks = kb._chunk_text(text)
                if not chunks:
                    stats["skipped"] += 1
                    continue

                meta = {
                    "source": "wechat_digest",
                    "article_id": aid,
                    "account": article.get("account"),
                    "title": article.get("title"),
                    "link": article.get("link"),
                    "cover": article.get("cover"),
                    "publish_date": article.get("publishDate"),
                    "collected_at": article.get("collectedAt"),
                    "tags": (article.get("analysis") or {}).get("tags", []),
                    "topics": _topic_names_for_article(aid, data),
                    "summary": (article.get("analysis") or {}).get("summary", ""),
                    "synced_at": datetime.now().isoformat(),
                }
                meta["chunks_meta"] = kb._generate_chunk_meta(chunks)
                meta_json = json.dumps(meta, ensure_ascii=False)

                doc_key = _doc_key(aid)

                if dry_run:
                    with kb.conn.cursor() as c:
                        c.execute(
                            "SELECT id FROM knowledge_docs WHERE filename = %s",
                            (doc_key,),
                        )
                        exists = c.fetchone()
                    stats["updated" if exists else "added"] += 1
                    continue

                embeddings = kb.embedder.encode_documents(chunks)

                with kb.conn.cursor() as c:
                    c.execute(
                        "SELECT id FROM knowledge_docs WHERE filename = %s",
                        (doc_key,),
                    )
                    row = c.fetchone()

                if row:
                    doc_id = row[0]
                    kb.vector_store.delete_by_doc(doc_id)
                    with kb.conn.cursor() as c:
                        c.execute(
                            """
                            UPDATE knowledge_docs
                            SET content = %s, metadata = %s, created_at = NOW()
                            WHERE id = %s
                            """,
                            (text, meta_json, doc_id),
                        )
                        kb.conn.commit()
                    kb.vector_store.add_chunks(doc_id, chunks, embeddings)
                    stats["updated"] += 1
                else:
                    with kb.conn.cursor() as c:
                        c.execute(
                            """
                            INSERT INTO knowledge_docs (filename, content, metadata)
                            VALUES (%s, %s, %s) RETURNING id
                            """,
                            (doc_key, text, meta_json),
                        )
                        doc_id = c.fetchone()[0]
                        kb.conn.commit()
                    kb.vector_store.add_chunks(doc_id, chunks, embeddings)
                    stats["added"] += 1

            except Exception as e:
                logger.error(f"同步文章 {aid} 失败: {e}", exc_info=True)
                stats["errors"] += 1
    finally:
        kb.close()

    return stats


def search(query: str, top_k: int = 5, min_similarity: float = 0.25) -> List[Dict]:
    """
    在已同步的 wechat-digest 知识库中做语义搜索。
    返回带标题、公众号、日期、链接、摘要的列表。
    """
    kb = KnowledgeBase()
    try:
        raw_results = kb.search(query, top_k=top_k * 2, min_similarity=min_similarity)
        if not raw_results:
            return []

        doc_ids = list(set(r["doc_id"] for r in raw_results))
        meta_by_id = {}
        with kb.conn.cursor() as c:
            for doc_id in doc_ids:
                c.execute("SELECT metadata FROM knowledge_docs WHERE id = %s", (doc_id,))
                row = c.fetchone()
                if not row or not row[0]:
                    continue
                try:
                    meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                    if meta.get("source") == "wechat_digest":
                        meta_by_id[doc_id] = meta
                except Exception:
                    continue

        seen = set()
        out = []
        for r in raw_results:
            doc_id = r["doc_id"]
            meta = meta_by_id.get(doc_id)
            if not meta:
                continue
            key = meta.get("article_id") or meta.get("link") or meta.get("title")
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "article_id": meta.get("article_id", ""),
                "title": meta.get("title", ""),
                "account": meta.get("account", ""),
                "publish_date": meta.get("publish_date", ""),
                "link": meta.get("link", ""),
                "summary": meta.get("summary", ""),
                "tags": meta.get("tags", []),
                "similarity": r.get("similarity", 0),
                "snippet": (r.get("chunk_text") or "")[:350],
            })
            if len(out) >= top_k:
                break
        return out
    finally:
        kb.close()


def get_stats() -> Dict:
    """返回已同步的 wechat-digest 文章/片段统计。"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT COUNT(*) FROM knowledge_docs
                WHERE metadata->>'source' = 'wechat_digest'
                """
            )
            docs = c.fetchone()[0]
            c.execute(
                """
                SELECT COUNT(*) FROM knowledge_embeddings e
                JOIN knowledge_docs d ON e.doc_id = d.id
                WHERE d.metadata->>'source' = 'wechat_digest'
                """
            )
            chunks = c.fetchone()[0]
        return {"articles": docs, "chunks": chunks}
    finally:
        conn.close()


def list_articles(limit: int = 50, offset: int = 0, account: str = "", tag: str = "") -> List[Dict]:
    """列出已同步的文章，支持按公众号和标签筛选。"""
    conn = get_conn()
    try:
        where = "WHERE metadata->>'source' = 'wechat_digest'"
        params = []
        if account:
            where += " AND metadata->>'account' = %s"
            params.append(account)
        if tag:
            where += " AND metadata->>'tags' LIKE %s"
            params.append(f"%{tag}%")

        sql = f"""
            SELECT id, filename, metadata, created_at
            FROM knowledge_docs
            {where}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])

        with conn.cursor() as c:
            c.execute(sql, params)
            rows = c.fetchall()

        articles = []
        for row in rows:
            try:
                meta = json.loads(row[1]) if isinstance(row[1], str) else row[1]
            except Exception:
                meta = {}
            articles.append({
                "doc_id": row[0],
                "article_id": meta.get("article_id", ""),
                "title": meta.get("title", ""),
                "account": meta.get("account", ""),
                "publish_date": meta.get("publish_date", ""),
                "link": meta.get("link", ""),
                "summary": meta.get("summary", ""),
                "tags": meta.get("tags", []),
                "topics": meta.get("topics", []),
                "synced_at": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
            })
        return articles
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        print(json.dumps(sync(path), ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(get_stats(), ensure_ascii=False))
    elif len(sys.argv) > 2 and sys.argv[1] == "search":
        print(json.dumps(search(sys.argv[2]), ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        print(json.dumps(list_articles(), ensure_ascii=False, indent=2))
    else:
        print("用法: python -m mind.wechat_digest sync [path] | stats | search <query> | list")
