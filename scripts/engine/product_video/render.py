import math
import subprocess
import tempfile
import threading

from .common import VideoError, executable, process_options
from .compositor import Renderer, fit_rect


def encode_video(renderer, audio, destination, timeout=7200, metadata_comment=None):
    v = renderer.v
    frame_count = math.ceil(renderer.total * v["fps"])
    args = [executable('ffmpeg'), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
            f"{v['width']}x{v['height']}", "-r", str(v["fps"]), "-i", "pipe:0", "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", v["encoder"]]
    args += ["-preset", "fast", "-crf", "18"] if v["encoder"] == "libx264" else ["-b:v", "8M"]
    args += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
             "-af", "apad", "-t", str(frame_count / v["fps"]), "-movflags", "+faststart",
             "-metadata", "comment=AI-generated narration; screenshot-based operation simulation", str(destination)]
    if metadata_comment is not None:
        args[args.index("-metadata") + 1] = "comment=" + metadata_comment
    elif all(c.get('timing_mode') == 'explicit-duration' for c in renderer.chapters):
        args[args.index('-metadata') + 1] = 'comment=Subtitle-only presentation; explicit display timing'
    elif any("scene3d" in s for c in renderer.chapters for s in c["steps"]):
        args[args.index("-metadata") + 1] = "comment=AI-generated narration; 3D device rendering with screen media"
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, **process_options())
        expired = threading.Event()
        def stop_encoding():
            expired.set()
            if proc.poll() is None:
                proc.kill()
        # Windows select() accepts sockets, not anonymous pipes. A watchdog
        # bounds the normal blocking pipe writer on both Windows and POSIX.
        timer = threading.Timer(timeout, stop_encoding)
        timer.daemon = True
        timer.start()
        try:
            for frame_index in range(frame_count):
                if expired.is_set():
                    raise VideoError('视频编码超时，已停止本次编码进程。')
                if frame_index % (v['fps'] * 5) == 0:
                    print(f"渲染 {frame_index / frame_count:.0%}（{frame_index / v['fps']:.0f}/{renderer.total:.0f} 秒）", flush=True)
                proc.stdin.write(renderer.frame(frame_index / v['fps']).tobytes())
            proc.stdin.close()
            proc.wait(timeout=120)
            if proc.returncode:
                raise VideoError(f"视频编码失败（退出码 {proc.returncode}）。")
        except (BrokenPipeError, subprocess.TimeoutExpired):
            if expired.is_set():
                raise VideoError('视频编码超时，已停止本次编码进程。') from None
            errors.seek(0)
            raise VideoError("视频编码中断：" + errors.read()[-1200:].decode(errors="replace")) from None
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
    return frame_count
