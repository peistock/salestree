"""
语音处理模块
- AMR 下载（企微媒体接口）
- FFmpeg 转码：AMR -> WAV
- Qwen3-ASR（MLX）语音识别
- Edge-TTS 语音合成（Phase 3 可选）
"""
import os
import subprocess
import logging

logger = logging.getLogger(__name__)

# 全局单例：ASR 会话懒加载
_asr_session = None


def _get_asr_session():
    """懒加载 Qwen3-ASR 模型"""
    global _asr_session
    if _asr_session is None:
        logger.info("正在加载 Qwen3-ASR 模型（首次较慢）...")
        from mlx_qwen3_asr import Session
        _asr_session = Session(model="Qwen/Qwen3-ASR-0.6B")
        logger.info("Qwen3-ASR 模型加载完成")
    return _asr_session


def amr_to_wav(amr_path: str, wav_path: str) -> bool:
    """AMR 转 WAV（16kHz 单声道，ASR 标准格式）"""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", amr_path,
            "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le", wav_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            logger.info(f"AMR 转 WAV 成功: {wav_path}")
            return True
        logger.error(f"FFmpeg 失败: {result.stderr}")
        return False
    except Exception as e:
        logger.error(f"转码异常: {e}")
        return False


def transcribe_audio(wav_path: str) -> str:
    """语音识别：Qwen3-ASR via mlx-qwen3-asr"""
    try:
        session = _get_asr_session()
        result = session.transcribe(wav_path)
        text = result.text.strip() if hasattr(result, "text") else str(result).strip()
        logger.info(f"语音识别结果: {text[:50]}...")
        return text
    except Exception as e:
        logger.error(f"语音识别失败: {e}")
        return ""


def text_to_speech(text: str, output_path: str) -> bool:
    """
    语音合成（Phase 3 可选）
    使用 Edge-TTS 生成 MP3，以文件形式发送到企微
    """
    # TODO: edge-tts --text "..." --voice zh-CN-XiaoxiaoNeural --write-media output.mp3
    return False
