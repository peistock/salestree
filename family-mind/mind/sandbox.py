"""
代码执行沙箱（Phase 2.5.1）
- 安全的 Python 代码执行（subprocess + timeout + 目录限制）
- 命令白名单 + 黑名单双重过滤
- pip 包安装（基础安全校验）
"""
import os
import re
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 命令黑名单（绝对禁止）
BLACKLIST_COMMANDS = {
    "rm", "rmdir", "mv", "dd", "mkfs", "fdisk", "format",
    "chmod", "chown", "sudo", "su", "passwd", "shadow",
    "curl", "wget", "nc", "netcat", "ssh", "scp", "ftp",
    "kill", "pkill", "killall", "reboot", "shutdown", "halt",
    "crontab", "at", "systemctl", "service", "launchctl",
}

# pip 包名黑名单（已知恶意或危险的包）
BLACKLIST_PACKAGES = {
    "requests", "urllib3", "http", "socket", "subprocess",
    "os", "sys", "shutil", "pathlib", "platform", "pwd", "grp",
}

# 允许写入的文件扩展名白名单
ALLOWED_EXTS = {".py", ".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".html", ".css", ".js"}


class Sandbox:
    """安全的代码执行沙箱"""

    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _is_safe_path(self, path: str) -> bool:
        """检查路径是否在工作目录内"""
        try:
            p = Path(path)
            if not p.is_absolute():
                p = self.work_dir / p
            resolved = p.resolve()
            return str(resolved).startswith(str(self.work_dir))
        except Exception:
            return False

    def _has_blacklist_cmd(self, code: str) -> Optional[str]:
        """检查代码中是否包含黑名单命令"""
        # 简单词法检查：检测 import os; os.system("rm") 等模式
        patterns = [
            r'os\.system\s*\(',
            r'subprocess\.call\s*\(',
            r'subprocess\.run\s*\(',
            r'subprocess\.Popen\s*\(',
            r'eval\s*\(',
            r'exec\s*\(',
            r'__import__\s*\(',
        ]
        for pattern in patterns:
            if re.search(pattern, code):
                return f"检测到危险调用: {pattern}"

        # 检查直接调用黑名单命令
        for cmd in BLACKLIST_COMMANDS:
            if re.search(rf'\b{cmd}\b', code):
                return f"检测到黑名单命令: {cmd}"
        return None

    def execute_python(self, code: str, filename: str = "script.py") -> dict:
        """
        执行 Python 代码
        - 代码写入临时文件后执行
        - timeout=30s
        - stdout/stderr 捕获返回
        """
        # 安全预审
        danger = self._has_blacklist_cmd(code)
        if danger:
            return {
                "status": "blocked",
                "stdout": "",
                "stderr": f"[安全拦截] {danger}",
                "returncode": -1,
            }

        # 写入临时文件
        script_path = self.work_dir / filename
        if not self._is_safe_path(str(script_path)):
            return {
                "status": "blocked",
                "stdout": "",
                "stderr": "[安全拦截] 文件路径超出工作目录",
                "returncode": -1,
            }

        try:
            script_path.write_text(code, encoding="utf-8")

            result = subprocess.run(
                ["python3", str(script_path)],
                cwd=str(self.work_dir),
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    "PYTHONPATH": str(self.work_dir),
                },
            )

            return {
                "status": "success" if result.returncode == 0 else "error",
                "stdout": result.stdout[:5000],  # 截断防止过大
                "stderr": result.stderr[:3000],
                "returncode": result.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "stdout": "",
                "stderr": "代码执行超时（30秒）",
                "returncode": -1,
            }
        except Exception as e:
            logger.error(f"沙箱执行失败: {e}")
            return {
                "status": "error",
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
            }

    def execute_command(self, command: str) -> dict:
        """
        执行 shell 命令（严格白名单）
        仅允许: python3, pip3, pandoc, wkhtmltopdf 等文档生成工具
        """
        cmd_parts = command.strip().split()
        if not cmd_parts:
            return {"status": "error", "stdout": "", "stderr": "空命令", "returncode": -1}

        base_cmd = cmd_parts[0]

        # 只允许白名单命令
        whitelist = {"python3", "pip3", "pandoc", "wkhtmltopdf", "markdown-pdf"}
        if base_cmd not in whitelist:
            return {
                "status": "blocked",
                "stdout": "",
                "stderr": f"[安全拦截] 命令 '{base_cmd}' 不在白名单中",
                "returncode": -1,
            }

        try:
            result = subprocess.run(
                cmd_parts,
                cwd=str(self.work_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "status": "success" if result.returncode == 0 else "error",
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:3000],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "stdout": "", "stderr": "命令执行超时", "returncode": -1}
        except Exception as e:
            return {"status": "error", "stdout": "", "stderr": str(e), "returncode": -1}

    def pip_install(self, package: str) -> dict:
        """
        安装 PyPI 包
        - 禁止安装黑名单包（系统/网络相关）
        - 禁止包含特殊字符的包名
        """
        package = package.strip().lower()

        # 基础校验
        if not re.match(r'^[a-z0-9_.\-]+$', package):
            return {"status": "blocked", "stdout": "", "stderr": "[安全拦截] 包名包含非法字符", "returncode": -1}

        if package in BLACKLIST_PACKAGES:
            return {"status": "blocked", "stdout": "", "stderr": f"[安全拦截] 包 '{package}' 在黑名单中", "returncode": -1}

        try:
            import sys
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "status": "success" if result.returncode == 0 else "error",
                "stdout": result.stdout[:3000],
                "stderr": result.stderr[:3000],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "stdout": "", "stderr": "pip 安装超时", "returncode": -1}
        except Exception as e:
            return {"status": "error", "stdout": "", "stderr": str(e), "returncode": -1}
