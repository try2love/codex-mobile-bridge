"""Admission and receipt projection for reconnecting mobile outboxes."""
import hashlib
import json
import re
import uuid


class DeliveryBlocked(ValueError):
    def __init__(self, message, *, wait=False):
        super().__init__(message)
        self.code = 'offline_delivery_wait' if wait else 'offline_delivery_rejected'


def request(query, gateway, sid, body=None):
    def field(name):
        values = query.get(name, [])
        if len(values) != 1:
            raise DeliveryBlocked('离线投递标识无效')
        return values[0]

    identifier, account = field('id'), field('account')
    try:
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError()
    except ValueError as exc:
        raise DeliveryBlocked('离线投递标识无效') from exc
    if not re.fullmatch(r'[0-9a-f]{64}', account) or not gateway or field('gatewayId') != gateway:
        raise DeliveryBlocked('离线投递的网关或账号已变化')
    if not isinstance(sid, str) or not sid or len(sid) > 512:
        raise DeliveryBlocked('离线会话标识无效')
    if body is not None:
        if (body.get('sessionId') != sid or body.get('id') != identifier or
                body.get('mode', 'send') != 'send' or body.get('workMode') not in (None, 'default', 'plan') or
                set(body) - {'id','sessionId','text','mode','workMode','skills','attachments','references','uiLocale','plugins'}):
            raise DeliveryBlocked('离线发件箱只支持普通发送')
        if any(key in body and not isinstance(body[key], list) for key in ('skills','attachments','references','plugins')):
            raise DeliveryBlocked('离线投递的附件或 Skill 格式无效')
        text = body.get('text')
        if not isinstance(text, str) or len(text) > 100000 or not (text.strip() or body.get('attachments') or body.get('references')):
            raise DeliveryBlocked('请输入 1–100000 字的消息')
    return identifier, account


def fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def require_intent(existing, supplied):
    if supplied is not None and existing != supplied:
        # The original request may already have been sent. This is not proof
        # that the caller may safely edit and replay it under another ID.
        raise ValueError('同一消息标识不能用于不同内容')


def waiting_for_turn(entry, turns, account):
    if entry.get('deliveryAccount') not in (None, account):
        return False
    if entry.get('status') in ('unknown', 'queued', 'uncertain'):
        return True
    if entry.get('status') != 'accepted' or 'deliveryBeforeTurn' not in entry:
        return False
    if not turns:
        return True
    latest = turns[-1]
    return latest.get('id') == entry['deliveryBeforeTurn'] or latest.get('status') not in (
        'completed', 'failed', 'interrupted', 'cancelled')


def proof(current, expected, *, ready=False, status='notFound'):
    return {'deliveryVersion': 1, 'account': current if current == expected else '',
            'ready': current == expected and ready and status == 'notFound',
            'status': status if current == expected else 'notFound'}


def admit(current, expected, ready, archived=False):
    if not current:
        raise DeliveryBlocked('暂时无法核验会话账号', wait=True)
    if current != expected or archived:
        raise DeliveryBlocked('离线会话的账号已变化或会话已归档')
    if not ready:
        raise DeliveryBlocked('请等待原会话空闲并可发送', wait=True)
