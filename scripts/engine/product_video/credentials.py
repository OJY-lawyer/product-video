import getpass
import json
import os
from pathlib import Path
import stat
import sys
import base64

from .common import VideoError, atomic_write


def credential_path():
    return Path.home() / ".config" / "product-video" / "credentials.json"


def save_key(key, path=None):
    path = path or credential_path()
    if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key):
        raise VideoError("密钥为空或含有空白字符，请重新输入。")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise VideoError("凭据路径不能是符号链接。")
    if os.name == 'nt':
        from .windows_secrets import crypt
        data = {'storage': 'windows-dpapi-user-v1', 'protected_key': base64.b64encode(crypt(key.encode('utf-8'))).decode('ascii')}
    else:
        os.chmod(path.parent, 0o700)
        data = {'api_key': key}
    atomic_write(path, json.dumps(data).encode('utf-8'), mode=0o600)


def configure():
    if not sys.stdin.isatty():
        raise VideoError("请在交互终端运行 credentials set，密钥输入不会回显。")
    save_key(getpass.getpass("Volcengine API key（不回显）: ").strip())
    print('已保存本地凭据（Windows 当前用户 DPAPI）' if os.name == 'nt' else '已保存本地凭据（0600）；后续自动读取。')


def load_key(path=None):
    path = path or credential_path()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or path.parent.is_symlink():
            raise VideoError('凭据路径必须为普通文件。')
        if os.name != 'nt' and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()):
            raise VideoError("凭据文件必须由当前用户持有且权限为 0600，请重新运行 credentials setup --replace。")
        data = json.loads(path.read_text(encoding="utf-8"))
        if os.name == 'nt':
            from .windows_secrets import crypt
            if data.get('storage') != 'windows-dpapi-user-v1':
                raise VideoError('此 Windows 凭据未采用当前用户保护；请重新运行 credentials setup --replace。')
            key = crypt(base64.b64decode(data['protected_key'], validate=True), decrypt=True).decode('utf-8')
        else:
            key = data['api_key']
        if not isinstance(key, str) or not key or any(c.isspace() for c in key):
            raise ValueError()
        return key
    except (OSError, ValueError, KeyError, TypeError):
        raise VideoError("本地凭据未配置或不可读，请运行 product-video credentials setup --replace。") from None


def is_configured(path=None):
    """Check ownership and mode without reading the credential value."""
    path = path or credential_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if os.name == 'nt':
        return stat.S_ISREG(info.st_mode) and not path.is_symlink() and not path.parent.is_symlink()
    return stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid()


def status():
    # Metadata only: checking status must never expose the stored value.
    path = credential_path()
    if not path.exists():
        print("尚未配置本地凭据。")
        return
    info = path.lstat()
    if os.name == 'nt':
        print('本地凭据文件已存在；Windows 使用当前用户 DPAPI 保护。有效性需通过 API 调用确认。')
    else:
        print(f"本地凭据文件已存在；权限 {stat.S_IMODE(info.st_mode):04o}。有效性需通过 API 调用确认。")
