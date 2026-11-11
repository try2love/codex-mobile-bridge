"""Generic-password bytes without password arguments or replacement ACLs."""
import shlex
import subprocess


def read(service, user):
    result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', service,
                             '-a', user, '-w'], capture_output=True, timeout=30)
    if result.returncode == 44:  # errSecItemNotFound, not access denial.
        return None
    if result.returncode:
        raise ValueError('无法读取系统钥匙串，请在电脑端检查授权')
    return result.stdout.removesuffix(b'\n')


def write(service, user, value):
    if value is None:
        result = subprocess.run(['/usr/bin/security', 'delete-generic-password', '-s', service,
                                 '-a', user], capture_output=True, timeout=30)
        if result.returncode not in (0, 44):
            raise ValueError('无法移除原系统凭据')
        return
    command = 'add-generic-password -U -s {} -a {} -w {}\n'.format(
        shlex.quote(service), shlex.quote(user), shlex.quote(value.decode('utf-8')))
    if len(command.encode()) > 4096:
        raise ValueError('原生钥匙串命令不支持此长度的登录凭据')
    result = subprocess.run(['/usr/bin/security', '-i'], input=command.encode(),
                            capture_output=True, timeout=30)
    # Interactive security can return success after an inner command fails.
    if result.returncode or read(service, user) != value:
        raise ValueError('系统钥匙串写入未确认，请在电脑端检查授权')
