import { randomUUID } from "crypto";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "../../..");
const UPLOAD_ROOT = path.join(PROJECT_ROOT, "data", "uploads");

export const ALLOWED_UPLOAD_TYPES = new Set([
  // images
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  // documents
  "application/pdf",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
]);

const MAX_UPLOAD_SIZE = 10 * 1024 * 1024; // 10 MB

export function sanitizeFileName(name: string): string {
  return name.replace(/[^a-zA-Z0-9_.一-龥-]/g, "_").replace(/_{2,}/g, "_");
}

export function getUploadDir(userId: string, threadId: string): string {
  const dir = path.join(UPLOAD_ROOT, sanitizeFileName(userId), sanitizeFileName(threadId));
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

export function urlFor(relativePath: string): string {
  // relativePath is like "<userId>/<threadId>/<filename>"
  return `/data/uploads/${relativePath.replace(/\\/g, "/")}`;
}

export interface StoredFile {
  name: string;
  url: string;
  mimeType: string;
  size: number;
  relativePath: string;
}

export function saveUpload(
  buffer: Buffer,
  originalName: string,
  mimeType: string,
  userId: string,
  threadId: string,
): StoredFile {
  if (!ALLOWED_UPLOAD_TYPES.has(mimeType)) {
    throw new Error(`不支持的文件类型：${mimeType}`);
  }
  if (buffer.length > MAX_UPLOAD_SIZE) {
    throw new Error(`文件大小超过 10MB 限制`);
  }

  const safeName = sanitizeFileName(originalName);
  const uniqueName = `${randomUUID().slice(0, 8)}-${safeName}`;
  const dir = getUploadDir(userId, threadId);
  const filePath = path.join(dir, uniqueName);
  fs.writeFileSync(filePath, buffer);

  const relativePath = path.relative(UPLOAD_ROOT, filePath);
  return {
    name: originalName,
    url: urlFor(relativePath),
    mimeType,
    size: buffer.length,
    relativePath,
  };
}

export function resolveUploadPath(relativePath: string): string {
  return path.join(UPLOAD_ROOT, relativePath);
}
