"""Select native installation primitives; session protocols remain shared."""
import sys
from pathlib import Path


def installation(application):
    path = Path(application)
    if sys.platform == 'darwin':
        from bridge.platforms.macos.discovery import bundle
        return bundle(path, 'dev.zcode.app')
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


def listener_identity(port, executable):
    if sys.platform == 'win32':
        from bridge.platforms.windows.loopback import listener_identity as read
    elif sys.platform == 'darwin':
        from bridge.platforms.macos.loopback import listener_identity as read
    else:
        raise ValueError('此平台的 ZCode 原生接入尚未验证')
    return read(port, executable)


def renderer_url(application):
    path = Path(application)
    if sys.platform == 'win32':
        return (path.parent/'resources/app.asar/out/renderer/index.html').as_uri()
    return (path/'Contents/Resources/app.asar/out/renderer/index.html').as_uri()


def launch(executable, home, port):
    arguments = ('--remote-debugging-address=127.0.0.1', '--remote-debugging-port='+str(port))
    if sys.platform == 'win32':
        from bridge.platforms.windows.session import require_interactive
        from bridge.platforms.windows.desktop import start
        require_interactive()
    elif sys.platform == 'darwin':
        from bridge.platforms.macos.desktop import start
    else:
        raise ValueError('此平台的 ZCode 原生接入尚未验证')
    return start(Path(executable), home, {}, arguments=arguments)
