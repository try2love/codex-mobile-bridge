"""Normalize Antigravity transcript.jsonl steps into Bridge timeline messages."""
from datetime import datetime
import json
from pathlib import Path
import re
import uuid

_USER_REQUEST_RE = re.compile(r'<USER_REQUEST>\s?(.*?)\s?</USER_REQUEST>', re.DOTALL)
_SYSTEM_TAGS_RE = re.compile(
    r'<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|EPHEMERAL_MESSAGE)>.*?'
    r'</(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|EPHEMERAL_MESSAGE)>',
    re.DOTALL,
)

SURFACE_LABELS = {
    'app': 'Antigravity 2.0',
    'ide': 'Antigravity IDE',
    'cli': 'Antigravity CLI',
}

# These reserve shared composer positions, never authorize native operations.
COMPOSER_PRESENTATION = dict.fromkeys(('models', 'skills', 'permissions', 'attachments', 'plan', 'goal'), True)
PERMISSION_OPTIONS = [
    {'value': 'desktop', 'label': '电脑端处理'},
    {'value': 'mobile-ask', 'label': '手机端审批'},
    {'value': 'auto-allow', 'label': '自动允许'},
]


def gateway_id(surface, conversation_id):
    """Return a deterministic 36-char UUID for a surface + native conversation ID."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'antigravity:{surface}:{conversation_id}'))


def extract_user_text(raw):
    """Strip Antigravity prompt wrappers while preserving exact user input."""
    if not isinstance(raw, str) or not raw:
        return ''
    match = _USER_REQUEST_RE.search(raw)
    if match:
        return match.group(1).strip()
    cleaned = _SYSTEM_TAGS_RE.sub('', raw)
    return cleaned.strip()


def _unquote_arg(val):
    """Unwrap JSON-encoded string values stored inside transcript tool_calls.args."""
    if not isinstance(val, str):
        return val
    text = val.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            parsed = json.loads(text)
            if isinstance(parsed, str):
                return parsed
        except ValueError:
            return text[1:-1]
    return text


def _clean_args(args):
    if not isinstance(args, dict):
        return {}
    return {k: _unquote_arg(v) for k, v in args.items()}


def _diff_preview(target, replacement):
    lines = []
    if isinstance(target, str) and target:
        for line in target.splitlines()[:60]:
            lines.append('- ' + line)
    if isinstance(replacement, str) and replacement:
        for line in replacement.splitlines()[:60]:
            lines.append('+ ' + line)
    return '\n'.join(lines)


def summarize_tool_title(tool_call):
    """Produce a concise native-style summary title (e.g. 'Ran ...', 'Read ...', 'Edited ...')."""
    if not isinstance(tool_call, dict):
        return '工具调用'
    name = str(tool_call.get('name') or 'tool')
    args = _clean_args(tool_call.get('args'))
    if name == 'run_command':
        summary = str(args.get('toolSummary') or '').strip()
        cmd = str(args.get('CommandLine') or args.get('command') or '').strip()
        if summary:
            return f'Ran {summary}'
        if cmd:
            short_cmd = cmd.splitlines()[0][:60]
            return f'Ran {short_cmd}'
        return 'Ran command'
    if name in ('read_file', 'view_file'):
        raw_path = str(args.get('Path') or args.get('AbsolutePath') or '').strip()
        base = Path(raw_path).name if raw_path else 'file'
        start = args.get('StartLine')
        end = args.get('EndLine')
        if start and end:
            return f'Read {base} (L{start}-{end})'
        return f'Read {base}'
    if name in ('edit_file', 'replace_file_content', 'multi_replace_file_content'):
        raw_path = str(args.get('Path') or args.get('TargetFile') or '').strip()
        base = Path(raw_path).name if raw_path else 'file'
        return f'Edited {base}'
    if name in ('write_file', 'write_to_file'):
        raw_path = str(args.get('Path') or args.get('TargetFile') or '').strip()
        base = Path(raw_path).name if raw_path else 'file'
        return f'Wrote {base}'
    return f'Ran {name}'


def format_tool_call(tool_call):
    """Format a tool_call dictionary into a readable activity block."""
    if not isinstance(tool_call, dict):
        return str(tool_call)
    name = str(tool_call.get('name') or 'tool')
    args = _clean_args(tool_call.get('args'))
    if not args:
        return name
    if name == 'run_command':
        summary = args.get('toolSummary') or ''
        cmd = args.get('CommandLine') or args.get('command') or ''
        header = f'# {summary}\n' if summary else ''
        return f'{header}$ {cmd}'.strip()
    if name in ('edit_file', 'replace_file_content'):
        path = args.get('Path') or args.get('TargetFile') or ''
        diff = _diff_preview(args.get('Target') or args.get('TargetContent', ''),
                             args.get('Replacement') or args.get('ReplacementContent', ''))
        return f'文件：{path}\n{diff}'.strip()
    if name == 'multi_replace_file_content':
        path = args.get('TargetFile') or args.get('Path') or ''
        chunks = args.get('ReplacementChunks') or []
        parts = [f'文件：{path}']
        if isinstance(chunks, list):
            for chunk in chunks[:10]:
                if isinstance(chunk, dict):
                    parts.append(_diff_preview(chunk.get('TargetContent', ''), chunk.get('ReplacementContent', '')))
        return '\n\n'.join(part for part in parts if part).strip()
    if name in ('write_file', 'write_to_file'):
        path = args.get('Path') or args.get('TargetFile') or ''
        content = str(args.get('Content') or args.get('CodeContent') or '')
        preview = '\n'.join(content.splitlines()[:40])
        if len(content.splitlines()) > 40:
            preview += '\n…'
        return f'写入文件：{path}\n{preview}'.strip()
    try:
        return json.dumps(args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(args)


def _step_error_text(step, default_text):
    err = step.get('error')
    if isinstance(err, dict):
        text = str(err.get('message') or err.get('detail') or err.get('reason') or '').strip()
    elif isinstance(err, str):
        text = err.strip()
    else:
        text = ''
    if not text:
        text = str(step.get('error_message') or step.get('message') or step.get('content') or '').strip()
    if len(text) > 4000:
        text = text[:4000] + '\n…（已截断）'
    return text or default_text


def _media_attachments(step_index, media):
    if not isinstance(media, list):
        return []
    attachments = []
    for idx, item in enumerate(media):
        if not isinstance(item, dict):
            continue
        mime = str(item.get('mime_type') or '')
        uri = str(item.get('uri') or '')
        if not mime.startswith('image/') or not uri:
            continue
        name = Path(uri.replace('file://', '')).name or f'image-{idx + 1}.png'
        attachments.append({
            'type': 'image',
            'kind': 'image',
            'name': name,
            '_nativeImage': {'stepIndex': int(step_index), 'mediaIndex': idx},
        })
    return attachments


def _parse_iso_seconds(ts):
    if not isinstance(ts, str) or not ts:
        return None
    text = ts.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    match = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?([+-]\d{2}:\d{2})?$', text)
    if match:
        base, frac, tz = match.groups()
        micros = (frac or '')[:6].ljust(6, '0')
        text = f'{base}.{micros}{tz or "+00:00"}'
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def normalize_steps(steps, *, session_active=False):
    """Convert ordered transcript steps into (messages, turns, context_usage)."""
    messages = []
    turns = []
    current_turn = None
    last_usage = None
    pending_tool_msgs = []
    prev_ts = None

    def ensure_turn(step_idx, status='completed'):
        nonlocal current_turn
        if current_turn is None:
            current_turn = {'turnId': f'turn-{step_idx}', 'status': status, 'messages': []}
            turns.append(current_turn)
        return current_turn

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_index = step.get('step_index', idx)
        step_type = str(step.get('type') or '')
        step_status = str(step.get('status') or 'DONE')
        step_ts = _parse_iso_seconds(step.get('created_at'))
        if step_ts is not None:
            step_ts *= 1000
        is_last = idx == len(steps) - 1

        if step_type == 'USER_INPUT':
            pending_tool_msgs.clear()
            text = extract_user_text(step.get('content') or '')
            attachments = _media_attachments(step_index, step.get('media'))
            msg = {'id': f'step-{step_index}-user', 'role': 'user', 'text': text, 'timestamp': step_ts}
            if attachments:
                msg['attachments'] = attachments
            current_turn = {'turnId': f'turn-{step_index}', 'status': 'completed', 'messages': [msg]}
            turns.append(current_turn)
            messages.append(msg)
            if step_ts is not None:
                prev_ts = step_ts
            continue

        turn = ensure_turn(step_index)
        if step_status in ('RUNNING', 'PENDING', 'IN_PROGRESS') or (is_last and session_active):
            turn['status'] = 'inProgress'
        elif step_status in ('ERROR', 'FAILED', 'CANCELED', 'CANCELLED'):
            turn['status'] = 'failed'

        if step_type == 'PLANNER_RESPONSE':
            pending_tool_msgs.clear()
            in_tok = step.get('input_tokens')
            cache_tok = step.get('cache_read_tokens') or 0
            cache_write_tok = step.get('cache_write_tokens') or 0
            out_tok = step.get('output_tokens') or 0
            if isinstance(in_tok, int) and in_tok >= 0:
                total_used = in_tok + (cache_tok if isinstance(cache_tok, int) else 0)
                last_usage = {
                    'usedTokens': total_used,
                    'contextWindow': None,
                    'measurement': 'session',
                    'outputTokens': out_tok if isinstance(out_tok, int) else 0,
                    'cache': {
                        'inputTokens': in_tok,
                        'cacheReadTokens': cache_tok if isinstance(cache_tok, int) else 0,
                        'cacheWriteTokens': cache_write_tok if isinstance(cache_write_tok, int) else 0,
                    },
                }
            thinking = step.get('thinking')
            if isinstance(thinking, str) and thinking.strip():
                think_title = '思考'
                msg = {
                    'id': f'step-{step_index}-thinking',
                    'role': 'reasoning', 'timestamp': step_ts,
                    'title': think_title,
                    'text': thinking.strip(),
                }
                messages.append(msg)
                turn['messages'].append(msg)

            tool_calls = step.get('tool_calls')
            if isinstance(tool_calls, list):
                for tc_idx, tc in enumerate(tool_calls):
                    msg = {
                        'id': f'step-{step_index}-tool-{tc_idx}',
                        'role': 'activity', 'timestamp': step_ts,
                        'title': summarize_tool_title(tc),
                        'text': format_tool_call(tc),
                    }
                    messages.append(msg)
                    turn['messages'].append(msg)
                    pending_tool_msgs.append(msg)

            content = step.get('content')
            attachments = _media_attachments(step_index, step.get('media'))
            if (isinstance(content, str) and content.strip()) or attachments:
                msg = {
                    'id': f'step-{step_index}-assistant',
                    'role': 'assistant', 'timestamp': step_ts,
                    'text': content.strip() if isinstance(content, str) else '',
                }
                if attachments:
                    msg['attachments'] = attachments
                messages.append(msg)
                turn['messages'].append(msg)
            elif step_status in ('ERROR', 'FAILED'):
                msg = {
                    'id': f'step-{step_index}-error',
                    'role': 'error',
                    'timestamp': step_ts,
                    'text': _step_error_text(step, '模型步骤执行异常'),
                }
                messages.append(msg)
                turn['messages'].append(msg)

        elif step_type in ('GENERIC', 'TOOL_RESPONSE'):
            content = step.get('content')
            preview = content.strip() if isinstance(content, str) and content.strip() else ''
            if not preview and step_status in ('ERROR', 'FAILED'):
                preview = _step_error_text(step, '')
            if len(preview) > 4000:
                preview = preview[:4000] + '\n…（已截断）'
            attachments = _media_attachments(step_index, step.get('media'))
            if preview or attachments:
                if pending_tool_msgs:
                    target_msg = pending_tool_msgs.pop(0)
                    if preview:
                        target_msg['text'] = f"{target_msg['text']}\n\n--- Output ---\n{preview}".strip()
                    if attachments:
                        target_msg.setdefault('attachments', []).extend(attachments)
                else:
                    msg = {
                        'id': f'step-{step_index}-output',
                        'role': 'activity', 'timestamp': step_ts,
                        'title': '工具输出',
                        'text': preview,
                    }
                    if attachments:
                        msg['attachments'] = attachments
                    messages.append(msg)
                    turn['messages'].append(msg)

        elif step_type == 'ERROR_MESSAGE':
            msg = {
                'id': f'step-{step_index}-error',
                'role': 'error',
                'timestamp': step_ts,
                'text': _step_error_text(step, '执行出错'),
            }
            messages.append(msg)
            turn['messages'].append(msg)

        elif step_type == 'CHECKPOINT':
            content = step.get('content')
            if isinstance(content, str) and content.strip():
                msg = {
                    'id': f'step-{step_index}-checkpoint',
                    'role': 'activity', 'timestamp': step_ts,
                    'title': '检查点',
                    'text': content.strip(),
                }
                messages.append(msg)
                turn['messages'].append(msg)

        if step_ts is not None:
            prev_ts = step_ts

    if session_active and turns:
        turns[-1]['status'] = 'inProgress'

    return messages, turns, last_usage
