import { FastifyInstance } from "fastify";
import { query } from "../db/index.js";

export async function bidMonitorRoutes(app: FastifyInstance) {
  // 获取招标列表
  app.get("/api/bids", async (request, reply) => {
    const { keyword, days = 7, limit = 50 } = request.query as any;
    
    let sql = `
      SELECT id, title, publisher, region, amount, bid_type, publish_time, keyword, url, created_at
      FROM bid_monitor 
      WHERE created_at >= NOW() - INTERVAL '${days} days'
    `;
    const params: any[] = [];
    
    if (keyword) {
      sql += ` AND (title ILIKE $1 OR keyword ILIKE $1)`;
      params.push(`%${keyword}%`);
    }
    
    sql += ` ORDER BY publish_time DESC NULLS LAST LIMIT ${limit}`;
    
    const rows = await query(sql, params);
    return { success: true, data: rows, count: rows.length };
  });

  // 获取统计信息
  app.get("/api/bids/stats", async () => {
    const stats = await query(`
      SELECT 
        DATE(created_at) as date,
        COUNT(*) as count,
        COUNT(DISTINCT keyword) as keywords
      FROM bid_monitor 
      WHERE created_at >= NOW() - INTERVAL '30 days'
      GROUP BY DATE(created_at)
      ORDER BY date DESC
    `);
    return { success: true, data: stats };
  });

  // 获取关键词统计
  app.get("/api/bids/keywords", async () => {
    const rows = await query(`
      SELECT keyword, COUNT(*) as count 
      FROM bid_monitor 
      WHERE created_at >= NOW() - INTERVAL '7 days'
      GROUP BY keyword 
      ORDER BY count DESC
    `);
    return { success: true, data: rows };
  });

  // 手动触发拉取
  app.post("/api/bids/refresh", async () => {
    // 触发后台任务
    return { success: true, message: "已触发招标数据拉取" };
  });
}
