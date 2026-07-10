"""
浏览器自动化封装（web-access CDP Proxy）
通过 HTTP API 调用 localhost:3456 的 CDP Proxy，操控独立 Chrome 实例。

独立 Chrome 启动方式：
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/chrome-familymind \
    --no-first-run --no-default-browser-check

CDP Proxy 启动方式：
  node ~/.claude/skills/web-access/scripts/cdp-proxy.mjs
"""
import os
import time
import logging
from pathlib import Path
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# CDP Proxy 地址
CDP_PROXY_URL = os.getenv("CDP_PROXY_URL", "http://localhost:3456")


class Browser:
    """通过 CDP Proxy HTTP API 操控浏览器"""

    def __init__(self, work_dir: Path = None, proxy_url: str = None):
        self.work_dir = work_dir or Path(os.getenv("DATA_DIR", "./data"))
        self.proxy_url = proxy_url or CDP_PROXY_URL
        self._target_id = None  # 当前 tab 的 target ID

    # ========== 内部 HTTP 调用 ==========

    def _request(self, method: str, path: str, data=None, params=None, timeout=30) -> dict:
        url = f"{self.proxy_url}{path}"
        try:
            proxies = {"http": None, "https": None}
            if method == "GET":
                resp = requests.get(url, params=params, timeout=timeout, proxies=proxies)
            elif method == "POST":
                resp = requests.post(url, data=data, params=params, timeout=timeout, proxies=proxies)
            else:
                raise ValueError(f"不支持的方法: {method}")
            resp.raise_for_status()
            # 尝试解析 JSON，失败则返回原始文本
            try:
                return resp.json()
            except ValueError:
                return {"_raw": resp.text}
        except requests.exceptions.ConnectionError:
            logger.error(f"无法连接 CDP Proxy ({self.proxy_url})，请检查 Proxy 是否已启动")
            raise RuntimeError("CDP Proxy 未启动，浏览器功能暂不可用")
        except Exception as e:
            logger.error(f"CDP Proxy 请求失败 [{path}]: {e}")
            raise

    # ========== 页面操作 ==========

    def open(self, url: str) -> str:
        """打开网页，返回标题和文字摘要"""
        try:
            # 1. 新建 tab
            result = self._request("GET", "/new", params={"url": url})
            self._target_id = result.get("targetId")
            if not self._target_id:
                return f"打开网页失败：{result}"

            logger.info(f"新建 tab: {self._target_id}")

            # 2. 等待页面加载
            time.sleep(2)

            # 3. 获取标题
            title_result = self._request(
                "POST", "/eval",
                params={"target": self._target_id},
                data="document.title"
            )
            title = title_result.get("value", "")

            # 4. 获取正文文字
            text_result = self._request(
                "POST", "/eval",
                params={"target": self._target_id},
                data="""
                (() => {
                    const scripts = document.querySelectorAll('script, style, nav, footer');
                    scripts.forEach(e => e.remove());
                    return document.body ? document.body.innerText : '';
                })()
                """
            )
            text = text_result.get("value", "")[:2000]

            return f"标题：{title}\n\n内容摘要（前 2000 字）：\n{text}"
        except RuntimeError as e:
            return str(e)
        except Exception as e:
            logger.error(f"打开网页失败 [{url}]: {e}")
            return f"打开网页失败：{e}"

    def click(self, selector: str) -> str:
        """点击页面元素"""
        if not self._target_id:
            return "（没有已打开的网页，请先调用 browse_open）"
        try:
            result = self._request(
                "POST", "/click",
                params={"target": self._target_id},
                data=selector
            )
            return f"已点击：{selector}"
        except Exception as e:
            return f"点击失败 [{selector}]：{e}"

    def fill(self, selector: str, text: str) -> str:
        """在输入框填写内容"""
        if not self._target_id:
            return "（没有已打开的网页，请先调用 browse_open）"
        try:
            # CDP Proxy 没有直接的 fill API，用 eval 实现
            esc_selector = selector.replace("'", "\\'")
            esc_text = text.replace("'", "\\'")
            js = f"""
            (() => {{
                const el = document.querySelector('{esc_selector}');
                if (!el) return '元素未找到';
                el.focus();
                el.value = '{esc_text}';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return '已填写';
            }})()
            """
            result = self._request(
                "POST", "/eval",
                params={"target": self._target_id},
                data=js
            )
            return f"已在 {selector} 填写内容"
        except Exception as e:
            return f"填写失败 [{selector}]：{e}"

    def screenshot(self, filename: str = None) -> str:
        """截图保存到工作目录"""
        if not self._target_id:
            return "（没有已打开的网页，请先调用 browse_open）"
        try:
            screenshots_dir = self.work_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)

            if not filename:
                filename = f"screenshot_{int(time.time())}.png"
            filepath = screenshots_dir / filename

            result = self._request(
                "GET", "/screenshot",
                params={"target": self._target_id, "file": str(filepath)}
            )
            saved = result.get("saved", str(filepath))
            return f"截图已保存：{saved}"
        except Exception as e:
            return f"截图失败：{e}"

    def scroll(self, direction: str = "down") -> str:
        """滚动页面"""
        if not self._target_id:
            return "（没有已打开的网页，请先调用 browse_open）"
        try:
            result = self._request(
                "GET", "/scroll",
                params={"target": self._target_id, "direction": direction}
            )
            return f"已滚动：{direction}"
        except Exception as e:
            return f"滚动失败：{e}"

    def get_text(self, selector: str = "body") -> str:
        """提取指定区域的文字"""
        if not self._target_id:
            return "（没有已打开的网页，请先调用 browse_open）"
        try:
            esc_selector = selector.replace("'", "\\'")
            js = f"""
            (() => {{
                const el = document.querySelector('{esc_selector}');
                return el ? el.innerText : '元素未找到';
            }})()
            """
            result = self._request(
                "POST", "/eval",
                params={"target": self._target_id},
                data=js
            )
            text = result.get("value", "")
            return text.strip()[:3000]
        except Exception as e:
            return f"提取文字失败 [{selector}]：{e}"

    def press(self, key: str) -> str:
        """按键（如 Enter、Escape）"""
        if not self._target_id:
            return "（没有已打开的网页，请先调用 browse_open）"
        try:
            js = f"""
            (() => {{
                const event = new KeyboardEvent('keydown', {{ key: '{key}', bubbles: true }});
                document.activeElement.dispatchEvent(event);
                return '已按下 {key}';
            }})()
            """
            result = self._request(
                "POST", "/eval",
                params={"target": self._target_id},
                data=js
            )
            return result.get("value", f"已按下 {key}")
        except Exception as e:
            return f"按键失败 [{key}]：{e}"

    def close(self):
        """关闭当前 tab"""
        if not self._target_id:
            return
        try:
            self._request("GET", "/close", params={"target": self._target_id})
            self._target_id = None
        except Exception as e:
            logger.warning(f"关闭 tab 失败: {e}")
