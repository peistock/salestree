import crypto from "crypto";
import { config } from "../config.ts";

function getKey(): Buffer | undefined {
  const key = config.userLlmEncryptionKey;
  if (!key) return undefined;
  return crypto.createHash("sha256").update(key).digest();
}

export function encryptApiKey(plaintext: string): string {
  const key = getKey();
  if (!key) return plaintext;
  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, nonce);
  const encrypted = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([nonce, encrypted, tag]).toString("base64");
}

export function decryptApiKey(ciphertext: string): string {
  const key = getKey();
  if (!key) return ciphertext;
  try {
    const data = Buffer.from(ciphertext, "base64");
    const nonce = data.subarray(0, 12);
    const tag = data.subarray(data.length - 16);
    const encrypted = data.subarray(12, data.length - 16);
    const decipher = crypto.createDecipheriv("aes-256-gcm", key, nonce);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString("utf8");
  } catch (e) {
    throw new Error(`API key 解密失败: ${e}`);
  }
}

export function maskApiKey(key: string): string {
  if (!key || key.length <= 8) return key;
  return key.slice(0, 4) + "****" + key.slice(-4);
}
