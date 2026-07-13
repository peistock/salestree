import { Embedder } from "./Embedder.ts";

const EMBED_TIMEOUT_MS = 60_000;
import { query } from "../db/index.ts";

export interface KnowledgeChunk {
  docId: number;
  chunkIndex: number;
  chunkText: string;
  filename: string;
  distance?: number;
}

export class VectorStore {
  async search(queryText: string, topK = 5): Promise<KnowledgeChunk[]> {
    let embedding: number[] | undefined;
    try {
      embedding = await this.embed(queryText);
    } catch {
      // 失败时回退到文本搜索
    }

    if (embedding) {
      const vectorLiteral = `[${embedding.join(",")}]`;
      const rows = await query<{
        doc_id: number;
        chunk_index: number;
        chunk_text: string;
        filename: string;
        distance: number;
      }>(
        `SELECT ke.doc_id, ke.chunk_index, ke.chunk_text, kd.filename,
                ke.embedding <=> $1::vector AS distance
         FROM knowledge_embeddings ke
         JOIN knowledge_docs kd ON ke.doc_id = kd.id
         ORDER BY ke.embedding <=> $1::vector
         LIMIT $2`,
        [vectorLiteral, topK]
      );
      return rows.map((r) => ({
        docId: r.doc_id,
        chunkIndex: r.chunk_index,
        chunkText: r.chunk_text,
        filename: r.filename,
        distance: r.distance,
      }));
    }

    // Fallback：文本搜索
    const rows = await query<{
      doc_id: number;
      chunk_index: number;
      chunk_text: string;
      filename: string;
    }>(
      `SELECT ke.doc_id, ke.chunk_index, ke.chunk_text, kd.filename
       FROM knowledge_embeddings ke
       JOIN knowledge_docs kd ON ke.doc_id = kd.id
       WHERE ke.chunk_text ILIKE $1
       LIMIT $2`,
      [`%${queryText}%`, topK]
    );
    return rows.map((r) => ({
      docId: r.doc_id,
      chunkIndex: r.chunk_index,
      chunkText: r.chunk_text,
      filename: r.filename,
    }));
  }

  private async embed(text: string): Promise<number[]> {
    const embedder = Embedder.getInstance();
    return await Promise.race([
      embedder.embed(text),
      new Promise<number[]>((_, reject) =>
        setTimeout(() => reject(new Error("embedding timeout")), EMBED_TIMEOUT_MS)
      ),
    ]);
  }
}
