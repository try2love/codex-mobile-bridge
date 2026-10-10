"""Native OS operations; application policies remain in their feature modules."""


def desktop(platform):
    if platform == 'win32':
        from .windows import desktop as native
    elif platform == 'linux':
        from .linux import desktop as native
    else:
        from .macos import desktop as native
    return native


def discovery(platform):
    if platform == 'darwin':
        from .macos import discovery as native
    elif platform == 'win32':
        from .windows import discovery as native
    else:
        from .linux import discovery as native
    return native
