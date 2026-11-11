"""TVPilot 语音合成层。

绞杀者模式阶段13迁移：EdgeTTS 语音合成 + 本地缓存 + LRU 裁剪。
"""
import os
import sys
import hashlib
import tempfile
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTS_CACHE_DIR = os.environ.get('TVPILOT_TTS_DIR', os.path.join(BASE_DIR, 'tts_cache'))
TTS_CACHE_MAX = int(os.environ.get('TVPILOT_TTS_CACHE_MAX', '200'))

try:
    os.makedirs(TTS_CACHE_DIR, exist_ok=True)
except Exception as _e:
    print(f'[TTS] 缓存目录不可用，语音缓存停用: {_e}', flush=True)


def _tts_cache_trim(max_files=TTS_CACHE_MAX):
    """按最近修改时间裁剪 TTS 缓存，只保留最新 max_files 个。"""
    try:
        files = [os.path.join(TTS_CACHE_DIR, f) for f in os.listdir(TTS_CACHE_DIR)
                 if f.endswith('.mp3')]
        if len(files) <= max_files:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for p in files[:len(files) - max_files]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def edge_tts_synthesize(text: str, voice: str = 'zh-CN-XiaoxiaoNeural'):
    """调用 edge-tts 生成语音，带本地缓存。返回 (audio_bytes, error)。"""
    if not text:
        return None, 'text empty'
    key = hashlib.md5(f'{text}|{voice}'.encode('utf-8')).hexdigest()
    cache_file = os.path.join(TTS_CACHE_DIR, f'{key}.mp3')
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 1000:
        with open(cache_file, 'rb') as f:
            return f.read(), None
    tmp_path = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()
        cmd = [
            sys.executable, '-m', 'edge_tts',
            '--voice', voice,
            '--text', text,
            '--write-media', tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, f'edge-tts failed: {result.stderr[:200]}'
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 1000:
            return None, 'audio too small'
        with open(tmp_path, 'rb') as f:
            audio = f.read()
        try:
            with open(cache_file, 'wb') as f:
                f.write(audio)
            _tts_cache_trim()
        except Exception:
            pass
        return audio, None
    except FileNotFoundError:
        return None, 'edge-tts not found'
    except Exception as e:
        return None, str(e)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


__all__ = [
    'TTS_CACHE_DIR', 'TTS_CACHE_MAX',
    '_tts_cache_trim', 'edge_tts_synthesize',
]
