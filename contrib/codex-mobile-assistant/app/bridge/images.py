"""Bounded image inputs, stored separately from the submission ledger."""
import base64
import binascii
import hashlib
import re
import uuid
from pathlib import Path

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MIME = {'png': 'image/png', 'jpeg': 'image/jpeg', 'webp': 'image/webp', 'gif': 'image/gif'}

def decode_image(url):
    if not isinstance(url, str) or len(url) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 40:
        raise ValueError('单张图片请压缩到 2 MB 以内')
    match = re.fullmatch(r'data:image/(png|jpeg|webp|gif);base64,([A-Za-z0-9+/=]+)', url)
    if not match:
        raise ValueError('图片只支持 PNG、JPEG、WebP 或 GIF')
    try:
        raw = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('图片数据无效') from exc
    kind = match[1]
    valid = (kind == 'png' and raw.startswith(b'\x89PNG\r\n\x1a\n') or
             kind == 'jpeg' and raw.startswith(b'\xff\xd8\xff') or
             kind == 'gif' and raw[:6] in (b'GIF87a', b'GIF89a') or
             kind == 'webp' and raw.startswith(b'RIFF') and raw[8:12] == b'WEBP')
    if not valid or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('图片格式不匹配或超过 2 MB')
    return raw, MIME[kind]

def prepare_images(images):
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError('每条消息最多添加 4 张图片')
    prepared = []
    for image in images:
        if not isinstance(image, dict):
            raise ValueError('图片数据无效')
        raw, mime = decode_image(image.get('url'))
        prepared.append(({'id': hashlib.sha256(raw).hexdigest(), 'mime': mime}, raw))
    return prepared

def image_path(data_dir, thread_id, image_id):
    uuid.UUID(thread_id)
    if not re.fullmatch(r'[0-9a-f]{64}', image_id):
        raise ValueError('图片标识无效')
    return Path(data_dir) / 'images' / thread_id / image_id

def save_images(data_dir, thread_id, prepared):
    for descriptor, raw in prepared:
        path = image_path(data_dir, thread_id, descriptor['id'])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            path.write_bytes(raw)
            path.chmod(0o600)

def image_inputs(data_dir, thread_id, descriptors):
    result = []
    for descriptor in descriptors:
        raw = image_path(data_dir, thread_id, descriptor['id']).read_bytes()
        if len(raw) > MAX_IMAGE_BYTES or hashlib.sha256(raw).hexdigest() != descriptor['id']:
            raise ValueError('图片文件已改变，请重新选择图片')
        result.append({'type': 'image', 'url': 'data:' + descriptor['mime'] + ';base64,' + base64.b64encode(raw).decode('ascii')})
    return result

def attachment_summary(attachment):
    summary = {key: str(attachment[key])[:300] for key in ('type', 'name', 'path') if key in attachment}
    if attachment.get('type') in ('image', 'input_image'):
        url = attachment.get('url') or attachment.get('image_url')
        if isinstance(url, dict):
            url = url.get('url')
        try:
            raw, _ = decode_image(url)
            summary['imageId'] = hashlib.sha256(raw).hexdigest()
        except ValueError:
            pass
    return summary
