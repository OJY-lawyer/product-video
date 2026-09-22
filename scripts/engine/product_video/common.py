import json
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import uuid


class VideoError(Exception):
    """An actionable error safe for the command-line boundary."""


def file_record(path):
    """Ordinary file metadata for cache freshness, never content verification."""
    state = Path(path).stat()
    return {'bytes': state.st_size, 'modified_ns': state.st_mtime_ns}


def cache_directory(parent, inputs):
    """Reuse an explicit matching input manifest; opaque names are random IDs."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    # JSON round-trip normalizes tuples and non-string dictionary keys.
    inputs = json.loads(json.dumps(inputs, ensure_ascii=False))
    for candidate in sorted(parent.iterdir()):
        manifest = candidate / 'inputs.json'
        if not candidate.is_dir() or not manifest.is_file():
            continue
        try:
            if json.loads(manifest.read_text(encoding='utf-8')) == inputs:
                return candidate
        except (OSError, ValueError):
            continue
    folder = parent / str(uuid.uuid4())
    folder.mkdir()
    write_json(folder / 'inputs.json', inputs)
    return folder


def atomic_write(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            if os.name != 'nt':
                os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_json(path, data):
    atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def run(argv, timeout=120):
    try:
        p = subprocess.run([executable(str(argv[0])), *[str(x) for x in argv[1:]]],
                           capture_output=True, timeout=timeout, **process_options())
    except FileNotFoundError:
        raise VideoError(f"缺少 {argv[0]}，请安装后重试。") from None
    except subprocess.TimeoutExpired:
        raise VideoError(f"{argv[0]} 超过 {timeout} 秒，已终止本次子进程。") from None
    if p.returncode:
        raise VideoError(f"{argv[0]} 失败（退出码 {p.returncode}）：{p.stderr.decode(errors='replace')[-1600:]}")
    return p.stdout


def executable(name):
    """Resolve optional per-process tools without changing the user's PATH."""
    configured = os.environ.get('PRODUCT_VIDEO_' + name.upper())
    return configured or shutil.which(name) or name


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path]))


def audio_duration(path):
    data = probe(path)
    if not any(s["codec_type"] == "audio" for s in data["streams"]):
        raise VideoError("音频文件没有音轨。")
    duration = float(data["format"]["duration"])
    if duration <= 0:
        raise VideoError("音频时长为零。")
    return duration
