"""Select native installation primitives; session protocols remain shared."""
import sys
from pathlib import Path


def installation(application):
    path = Path(application)
    if sys.platform == 'darwin':
        from bridge.platforms.macos.discovery import bundle
        return bundle(path, 'dev.zcode.app')
    if sys.platform == 'linux':
        from bridge.platforms.linux.zcode_pipe import installation as linux_installation
        return linux_installation(path)
    if sys.platform == 'win32':
        from bridge.platforms.windows.executable import product_version
        if not path.is_absolute() or path.name.lower() != 'zcode.exe' or not path.is_file():
            return None
        path = path.resolve()
        if not (path.parent/'resources/app.asar').is_file():
            return None
        version = product_version(path)
        if version:
            return {'executable': str(path), 'version': '.'.join(version.split('.')[:3])}
    return None


def listener_identity(port, executable, *, required=True):
    if sys.platform == 'win32':
        from bridge.platforms.windows.loopback import listener_identity as read
    elif sys.platform == 'darwin':
        from bridge.platforms.macos.loopback import listener_identity as read
    else:
        raise ValueError('此平台的 ZCode 原生接入尚未验证')
    return read(port, executable, required=required)


def renderer_url(application):
    path = Path(application)
    if sys.platform in ('win32', 'linux'):
        return (path.parent/'resources/app.asar/out/renderer/index.html').as_uri()
    return (path/'Contents/Resources/app.asar/out/renderer/index.html').as_uri()


def launch(executable, home, *, debug_port=None):
    if sys.platform == 'linux':
        from bridge.platforms.linux.zcode_pipe import launch_ordinary
        if debug_port is not None:
            raise ValueError('Linux ZCode 使用私有会话连接')
        return launch_ordinary(executable, home)
    if sys.platform == 'win32':
        from bridge.platforms.windows.session import require_interactive
        from bridge.platforms.windows.desktop import start
        require_interactive()
    elif sys.platform == 'darwin':
        from bridge.platforms.macos.desktop import start
    else:
        raise ValueError('此平台的 ZCode 原生接入尚未验证')
    directory = home if home.is_dir() else Path(executable).parent
    if debug_port is None:
        return start(Path(executable), directory, {})
    if debug_port != 17742:
        raise ValueError('ZCode 兼容连接设置无效')
    return start(Path(executable), directory, {}, arguments=(
        '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=17742'))
