"""Live gateway-wide policy for ordinary file-link downloads."""
from pathlib import Path
from bridge.features.notifications.channels import read_json, write_json

DEFAULT_MIB = 20


def transfer_settings(directory, value=None):
    if directory is None:
        if value is not None: raise ValueError('文件传输设置暂不可用')
        return {'clickDownloadMiB': DEFAULT_MIB}
    path = Path(directory) / 'file-transfer.json'
    if value is not None:
        if (not isinstance(value, dict) or set(value) != {'clickDownloadMiB'}
                or type(value['clickDownloadMiB']) is not int
                or not 1 <= value['clickDownloadMiB'] <= 1048576):
            raise ValueError('下载阈值须为 1–1048576 MiB 的整数')
        write_json(path, value)
    result = read_json(path, {'clickDownloadMiB': DEFAULT_MIB})
    return {'clickDownloadMiB': result['clickDownloadMiB']}
