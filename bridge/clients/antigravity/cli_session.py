"""Use the verified original Antigravity server; never spawn a competing CLI."""
import copy
import hashlib
import json
import logging
import re
import math
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
import subprocess
import time
import urllib.error
import urllib.request

from bridge.clients.errors import BridgeUnavailable, ResumePreflightBlocked
from bridge.platforms.antigravity import endpoint, verify_endpoint
from .model import SURFACE_LABELS, format_tool_call, _parse_iso_seconds
from .store import SURFACE_DIRS, local_file_path
from .quota import quota_groups, model_windows, overlay as overlay_quota

_CONFIRM_INTERACTION_LABELS = {
    'openBrowserUrl': '开启网页确认',
    'readUrlContent': '读取网页确认',
    'sendCommandInput': '发送终端输入确认',
    'mcp': 'MCP 工具执行确认',
    'approvalInteraction': '等待确认',
    'runExtensionCode': '执行扩展代码确认',
    'executeBrowserJavascript': '执行浏览器脚本确认',
    'captureBrowserScreenshot': '截取浏览器画面确认',
    'clickBrowserPixel': '浏览器点击确认',
    'browserAction': '浏览器操作确认',
    'openBrowserSetup': '开启浏览器设置确认',
    'confirmBrowserSetup': '浏览器设置确认',
}

_STEP_TYPE_TO_INTERACTION = {
    'CORTEX_STEP_TYPE_ASK_QUESTION': 'askQuestion',
    'CORTEX_STEP_TYPE_RUN_COMMAND': 'runCommand',
    'CORTEX_STEP_TYPE_OPEN_BROWSER_URL': 'openBrowserUrl',
    'CORTEX_STEP_TYPE_READ_URL_CONTENT': 'readUrlContent',
    'CORTEX_STEP_TYPE_SEND_COMMAND_INPUT': 'sendCommandInput',
    'CORTEX_STEP_TYPE_MCP_TOOL': 'mcp',
    'CORTEX_STEP_TYPE_RUN_EXTENSION_CODE': 'runExtensionCode',
    'CORTEX_STEP_TYPE_EXECUTE_BROWSER_JAVASCRIPT': 'executeBrowserJavascript',
    'CORTEX_STEP_TYPE_CAPTURE_BROWSER_SCREENSHOT': 'captureBrowserScreenshot',
    'CORTEX_STEP_TYPE_CLICK_BROWSER_PIXEL': 'clickBrowserPixel',
}

_INTERACTION_KINDS = (
    'askQuestion',
    'runCommand',
    'filePermission',
    'permission',
    'elicitation',
    'deploy',
    *tuple(_CONFIRM_INTERACTION_LABELS.keys()),
)

_ACTIVE_RUN_STATUSES = {'CASCADE_RUN_STATUS_RUNNING', 'CASCADE_RUN_STATUS_BUSY'}

_SCOPE_BY_DECISION = {
    'accept': 'PERMISSION_SCOPE_ONCE',
    'accept_once': 'PERMISSION_SCOPE_ONCE',
    'allow': 'PERMISSION_SCOPE_ONCE',
    'once': 'PERMISSION_SCOPE_ONCE',
    'yes': 'PERMISSION_SCOPE_ONCE',
    'accept_conversation': 'PERMISSION_SCOPE_CONVERSATION',
    'accept_workspace': 'PERMISSION_SCOPE_WORKSPACE',
    'accept_project': 'PERMISSION_SCOPE_PROJECT',
    'accept_global': 'PERMISSION_SCOPE_GLOBAL',
}

_DECLINE_DECISIONS = {'decline', 'deny', 'cancel', 'cancelled', 'reject', 'no'}
_ACCEPT_DECISIONS = {'accept', 'accept_once', 'allow', 'once', 'yes'}


def make_waiting_request_id(surface, cascade_id, trajectory_id, step_index):
    """Build a surface-scoped request ID for a waiting Language Server step."""
    return f'agy-wait:{surface}:{cascade_id}:{trajectory_id}:{int(step_index)}'


def parse_waiting_request_id(request_id):
    """Parse (surface, cascade_id, trajectory_id, step_index) from an `agy-wait:` ID."""
    if not isinstance(request_id, str) or not request_id.startswith('agy-wait:'):
        return None
    tail = request_id[len('agy-wait:'):]
    head, _, rest = tail.partition(':')
    if head in SURFACE_LABELS and ':' in rest:
        parts = rest.rsplit(':', 2)
        if len(parts) == 3 and parts[0] and parts[1] and parts[2].isdigit():
            return head, parts[0], parts[1], int(parts[2])
        return None
    parts = tail.rsplit(':', 2)
    if len(parts) == 3 and parts[0] and parts[1] and parts[2].isdigit():
        return None, parts[0], parts[1], int(parts[2])
    return None


def _non_empty_dict(**items):
    return {k: v for k, v in items.items() if v not in (None, '', [], {})}


def _extract_waiting_kind(step):
    """Identify the active RequestedInteraction variant on a waiting step."""
    requested = step.get('requestedInteraction')
    if isinstance(requested, dict):
        for key in _INTERACTION_KINDS:
            if requested.get(key) is not None:
                return key
    step_type = str(step.get('type') or '')
    if step_type in _STEP_TYPE_TO_INTERACTION:
        return _STEP_TYPE_TO_INTERACTION[step_type]
    if isinstance(step.get('askQuestion'), dict):
        return 'askQuestion'
    if isinstance(step.get('runCommand'), dict):
        return 'runCommand'
    return ''


def _schema_to_elicitation_questions(schema_raw):
    """Translate an MCP elicitation JSON schema into bridge question items."""
    if not isinstance(schema_raw, str) or not schema_raw.strip():
        return [], {}
    try:
        schema = json.loads(schema_raw)
    except ValueError:
        return [], {}
    if (not isinstance(schema, dict) or schema.get('type') != 'object'
            or set(schema) - {'type', 'properties', 'required', 'title', 'description', '$schema', 'additionalProperties'}
            or schema.get('additionalProperties', False) is not False):
        return [], {}
    props = schema.get('properties')
    if not isinstance(props, dict) or not props:
        return [], {}
    required = schema.get('required', [])
    if not isinstance(required, list) or any(not isinstance(k, str) or k not in props for k in required):
        return [], {}
    questions = []
    prop_specs = {}
    for prop_name, prop_spec in props.items():
        if not _supported_elicitation_property(prop_spec):
            return [], {}
        title = str(prop_spec.get('title') or prop_name)
        desc = str(prop_spec.get('description') or '').strip()
        label = f'{title} — {desc}' if desc else title
        prop_type = prop_spec['type']
        value_spec = prop_spec['items'] if prop_type == 'array' else prop_spec
        enum_vals = value_spec.get('enum')
        options = []
        if isinstance(enum_vals, list) and enum_vals:
            options = [{'id': str(v).lower() if isinstance(v, bool) else str(v),
                        'label': str(v).lower() if isinstance(v, bool) else str(v)} for v in enum_vals]
        elif prop_type == 'boolean':
            options = [{'id': 'true', 'label': '是 (true)'}, {'id': 'false', 'label': '否 (false)'}]
        questions.append({
            'id': prop_name,
            'question': label,
            'multiSelect': prop_type == 'array',
            'canSkip': prop_name not in required,
            'options': options,
            'allowCustom': not options,
        })
        prop_specs[prop_name] = prop_spec
    return questions, prop_specs


def _supported_elicitation_property(spec, *, item=False):
    if not isinstance(spec, dict):
        return False
    kind = spec.get('type')
    common = {'type', 'title', 'description', 'default', 'enum'}
    fields = {'string': {'minLength', 'maxLength'}, 'integer': {'minimum', 'maximum'},
              'number': {'minimum', 'maximum'}, 'boolean': set(),
              'array': {'items', 'minItems', 'maxItems'}}
    if not isinstance(kind, str) or kind not in fields or set(spec) - common - fields[kind]:
        return False
    for key in fields[kind] - {'items'}:
        if key in spec and (type(spec[key]) not in (int, float) or not math.isfinite(spec[key])
                            or (key not in ('minimum', 'maximum') and (type(spec[key]) is not int or spec[key] < 0))):
            return False
    if kind == 'array':
        return (not item and 'enum' not in spec and _supported_elicitation_property(spec.get('items'), item=True)
                and isinstance(spec['items'].get('enum'), list) and bool(spec['items']['enum']))
    enum = spec.get('enum')
    return enum is None or (isinstance(enum, list) and bool(enum)
                           and all(isinstance(v, (str, bool, int, float)) for v in enum))


def _normalize_ask_question(request_id, prefix, step, requested, meta):
    raw_questions = (
        (requested.get('askQuestion') or {}).get('questions')
        or (step.get('askQuestion') or {}).get('questions')
        or []
    )
    if not raw_questions:
        return None, None
    mapped = []
    for idx, q in enumerate(raw_questions):
        if not isinstance(q, dict):
            continue
        opts = []
        for opt_idx, opt in enumerate(q.get('options') or []):
            if not isinstance(opt, dict):
                continue
            opt_id = str(opt.get('id') or (opt_idx + 1))
            opt_text = str(opt.get('text') or opt_id)
            opts.append({'id': opt_id, 'label': opt_text})
        mapped.append({
            'id': str(idx),
            'question': str(q.get('question') or f'问题 {idx + 1}'),
            'multiSelect': bool(q.get('isMultiSelect')),
            'canSkip': True,
            'options': opts,
        })
    meta['questions'] = [q for q in raw_questions if isinstance(q, dict)]
    public = {
        'id': request_id,
        'tool': f'{prefix}提问',
        'needsInput': True,
        'canCancel': True,
        'cancelLabel': '取消',
        'exclusiveWriteIn': True,
        'input': {'questions': mapped},
    }
    return public, meta


def _normalize_elicitation(request_id, prefix, requested, meta):
    spec = requested.get('elicitation') if isinstance(requested.get('elicitation'), dict) else {}
    questions, prop_specs = _schema_to_elicitation_questions(spec.get('requestedSchemaJson'))
    server_name = str(spec.get('serverName') or 'MCP')
    message = str(spec.get('message') or '').strip()
    if questions:
        if message:
            questions[0] = {**questions[0], 'question': f"{message}\n{questions[0]['question']}"}
        meta['elicitationProps'] = prop_specs
        meta['elicitationRequired'] = [q['id'] for q in questions if not q['canSkip']]
        return {
            'id': request_id,
            'tool': f'{prefix}MCP 信息补充 · {server_name}',
            'needsInput': True,
            'canCancel': True,
            'cancelLabel': '拒绝',
            'exclusiveWriteIn': True,
            'input': {'questions': questions},
        }, meta
    # Unsupported forms must not silently turn into an approval with no data.
    unsupported = spec.get('mode') != 'url' or bool(spec.get('requestedSchemaJson'))
    meta['elicitationUnsupported'] = unsupported
    return {
        'id': request_id,
        'tool': f'{prefix}MCP 授权确认 · {server_name}',
        'needsInput': False,
        'input': _non_empty_dict(server=server_name, mode=spec.get('mode'), message=message, url=spec.get('url'),
                                 notice='此表单请在原客户端完成' if unsupported else None),
        'decisions': ([] if unsupported else [{'id': 'accept', 'label': '允许本次'}]) + [{'id': 'decline', 'label': '拒绝'}],
    }, meta


def _permission_decisions(scope_filter):
    decisions = [{'id': 'accept_once', 'label': '允许本次'}]
    if not scope_filter or 'PERMISSION_SCOPE_CONVERSATION' in scope_filter:
        decisions.append({'id': 'accept_conversation', 'label': '本会话允许'})
    if 'PERMISSION_SCOPE_WORKSPACE' in scope_filter:
        decisions.append({'id': 'accept_workspace', 'label': '本工作区允许'})
    decisions.append({'id': 'decline', 'label': '拒绝'})
    return decisions


def _normalize_waiting_step(request_id, surface, cascade_id, trajectory_id, step_index, step, *, root_cid=None, parent_cid=None):
    """Convert an Antigravity waiting step into (public_request, internal_meta)."""
    kind = _extract_waiting_kind(step)
    if not kind:
        return None, None
    requested = step.get('requestedInteraction') if isinstance(step.get('requestedInteraction'), dict) else {}
    prefix = '子智能体 · ' if (root_cid and cascade_id != root_cid) else ''
    meta = {
        'requestId': request_id,
        'surface': surface,
        'cascadeId': cascade_id,
        'trajectoryId': trajectory_id,
        'stepIndex': int(step_index),
        'rootCid': root_cid or cascade_id,
        'parentCid': parent_cid or '',
        'kind': kind,
        'step': step,
        'requestedInteraction': requested,
    }
    if kind == 'askQuestion':
        return _normalize_ask_question(request_id, prefix, step, requested, meta)
    if kind == 'elicitation':
        return _normalize_elicitation(request_id, prefix, requested, meta)
    if kind == 'runCommand':
        rc = step.get('runCommand') if isinstance(step.get('runCommand'), dict) else {}
        proposed = str(rc.get('proposedCommandLine') or rc.get('commandLine') or rc.get('command') or '').strip()
        meta['proposedCommandLine'] = proposed
        return {
            'id': request_id,
            'tool': f'{prefix}执行终端命令',
            'needsInput': False,
            'input': _non_empty_dict(command=proposed, cwd=str(rc.get('cwd') or '').strip()),
            'decisions': [{'id': 'accept', 'label': '允许执行'}, {'id': 'decline', 'label': '拒绝'}],
        }, meta
    if kind == 'filePermission':
        spec = requested.get('filePermission') or (step.get('codeAction') or {}).get('filePermissionRequest') or {}
        uri = str(spec.get('absolutePathUri') or '').strip()
        meta['absolutePathUri'] = uri
        return {
            'id': request_id,
            'tool': f'{prefix}文件访问权限',
            'needsInput': False,
            'input': _non_empty_dict(path=uri, directory=True if spec.get('isDirectory') else None, reason=str(spec.get('blockReason') or '').strip()),
            'decisions': [{'id': 'accept_once', 'label': '允许本次'}, {'id': 'accept_conversation', 'label': '本会话允许'}, {'id': 'decline', 'label': '拒绝'}],
        }, meta
    if kind == 'permission':
        spec = requested.get('permission') if isinstance(requested.get('permission'), dict) else {}
        res = spec.get('resource') if isinstance(spec.get('resource'), dict) else {}
        return {
            'id': request_id,
            'tool': f'{prefix}操作权限确认',
            'needsInput': False,
            'input': _non_empty_dict(action=res.get('action'), target=res.get('target'), reason=spec.get('actionDescription') or spec.get('reason')),
            'decisions': _permission_decisions(list(spec.get('approvalScopeFilter') or [])),
        }, meta
    if kind == 'deploy':
        spec = requested.get('deploy') if isinstance(requested.get('deploy'), dict) else {}
        targets = [t for t in (spec.get('deployTargetOptions') or []) if isinstance(t, dict)]
        meta['deployTargets'] = targets
        decisions = [{'id': f'deploy_{idx}', 'label': str(t.get('serviceName') or t.get('name') or t.get('platform') or f'部署目标 {idx + 1}')} for idx, t in enumerate(targets)]
        decisions = decisions or [{'id': 'accept', 'label': '允许本次'}]
        decisions.append({'id': 'decline', 'label': '拒绝'})
        return {'id': request_id, 'tool': f'{prefix}部署确认', 'needsInput': False, 'input': _non_empty_dict(targets=targets), 'decisions': decisions}, meta

    label = _CONFIRM_INTERACTION_LABELS.get(kind, '等待确认')
    tool_call = (step.get('metadata') or {}).get('toolCall')
    mcp_tool = step.get('mcpTool') if isinstance(step.get('mcpTool'), dict) else {}
    sci = step.get('sendCommandInput') if isinstance(step.get('sendCommandInput'), dict) else {}
    url_payload = step.get('openBrowserUrl') or step.get('readUrlContent') or {}
    summary = ''
    if isinstance(mcp_tool.get('toolCall'), dict):
        summary = format_tool_call(mcp_tool['toolCall'])
    elif isinstance(tool_call, dict):
        summary = format_tool_call(tool_call)
    detail = _non_empty_dict(
        url=url_payload.get('url') if isinstance(url_payload, dict) else None,
        server=mcp_tool.get('serverName'),
        tool=(mcp_tool.get('toolCall') or {}).get('name') if isinstance(mcp_tool.get('toolCall'), dict) else None,
        commandId=sci.get('commandId'),
        input=sci.get('input'),
        terminate=True if sci.get('terminate') else None,
        summary=summary,
    ) or _non_empty_dict(stepType=step.get('type') or kind)
    return {
        'id': request_id,
        'tool': f'{prefix}{label}',
        'needsInput': False,
        'input': detail,
        'decisions': [{'id': 'accept', 'label': '允许本次'}, {'id': 'decline', 'label': '拒绝'}],
    }, meta


def _coerce_elicitation_value(raw_vals, prop_spec):
    if not isinstance(raw_vals, list) or not raw_vals or any(not isinstance(v, str) for v in raw_vals):
        raise ValueError('回答格式无效')
    prop_type = prop_spec['type']
    if prop_type == 'array':
        values = [_coerce_elicitation_value([v], prop_spec['items']) for v in raw_vals]
        if len(values) < prop_spec.get('minItems', 0) or len(values) > prop_spec.get('maxItems', len(values)):
            raise ValueError('选择数量不符合要求')
        return values
    if len(raw_vals) != 1:
        raise ValueError('此项只能填写一个答案')
    val = raw_vals[0] if prop_type == 'string' else raw_vals[0].strip()
    if prop_type == 'boolean':
        if val.lower() not in ('true', 'false', '是 (true)', '否 (false)'):
            raise ValueError('请选择是或否')
        val = val.lower() in ('true', '是 (true)')
    elif prop_type in ('integer', 'number'):
        val = int(val) if prop_type == 'integer' else float(val)
        if not math.isfinite(val) or val < prop_spec.get('minimum', val) or val > prop_spec.get('maximum', val):
            raise ValueError('数字不符合要求')
    elif prop_type == 'string':
        if len(val) < prop_spec.get('minLength', 0) or len(val) > prop_spec.get('maxLength', len(val)):
            raise ValueError('文本长度不符合要求')
    else:
        raise ValueError('此回答类型请在原客户端处理')
    if 'enum' in prop_spec and not any(val == v and (type(val) is type(v)
            or (type(val) in (int, float) and type(v) in (int, float))) for v in prop_spec['enum']):
        raise ValueError('请选择问题中提供的选项')
    return val


def _build_ask_question_response(meta, is_decline, answers):
    if is_decline:
        return {'askQuestion': {'cancelled': True}}
    if not isinstance(answers, dict):
        raise ValueError('请回答每一项问题')
    responses = []
    for idx, orig_q in enumerate(meta.get('questions') or []):
        q_text = str(orig_q.get('question') or '')
        opts = [o for o in (orig_q.get('options') or []) if isinstance(o, dict)]
        ans_entry = answers.get(str(idx))
        if not isinstance(ans_entry, dict) and q_text:
            ans_entry = answers.get(q_text)
        if isinstance(ans_entry, dict) and ans_entry.get('skipped') is True:
            item = {'question': q_text, 'options': list(opts), 'skipped': True}
            if orig_q.get('isMultiSelect'):
                item['isMultiSelect'] = True
            responses.append(item)
            continue
        raw_vals = ans_entry.get('answers') if isinstance(ans_entry, dict) else None
        cleaned_vals = [str(v).strip() for v in (raw_vals if isinstance(raw_vals, list) else []) if isinstance(v, str) and str(v).strip()]
        if not cleaned_vals:
            raise ValueError('请回答每一项问题')
        text_to_id = {}
        valid_ids = set()
        for opt_idx, opt in enumerate(opts):
            opt_id = str(opt.get('id') or (opt_idx + 1))
            text_to_id[str(opt.get('text') or opt_id)] = opt_id
            valid_ids.add(opt_id)
        selected_ids, write_in_parts = [], []
        for val in cleaned_vals:
            if val in text_to_id:
                oid = text_to_id[val]
                if oid not in selected_ids:
                    selected_ids.append(oid)
            elif val in valid_ids and not any(str(o.get('text') or '') == val for o in opts):
                if val not in selected_ids:
                    selected_ids.append(val)
            else:
                write_in_parts.append(val)
        write_in = '\n'.join(write_in_parts).strip()
        if not orig_q.get('isMultiSelect'):
            selected_ids = [] if write_in else selected_ids[:1]
        item = {'question': q_text, 'options': list(opts)}
        if orig_q.get('isMultiSelect'):
            item['isMultiSelect'] = True
        if selected_ids:
            item['selectedOptionIds'] = selected_ids
        if write_in:
            item['writeInResponse'] = write_in
        responses.append(item)
    return {'askQuestion': {'responses': responses, 'cancelled': False}}


def _build_interaction_response(meta, decision, answers=None):
    """Build the `CascadeUserInteraction` body for HandleCascadeUserInteraction."""
    kind = meta['kind']
    decision_norm = str(decision or '').strip().lower()
    is_decline = decision_norm in _DECLINE_DECISIONS

    if kind == 'askQuestion':
        return _build_ask_question_response(meta, is_decline, answers)
    if kind == 'runCommand':
        if decision_norm not in _ACCEPT_DECISIONS and not is_decline:
            raise ValueError('请选择允许执行或拒绝')
        proposed = meta.get('proposedCommandLine') or ''
        return {'runCommand': {'confirm': not is_decline, 'proposedCommandLine': proposed, 'submittedCommandLine': proposed}}
    if kind == 'filePermission':
        uri = meta.get('absolutePathUri') or ''
        if is_decline:
            return {'filePermission': {'allow': False, 'scope': 'PERMISSION_SCOPE_ONCE', 'absolutePathUri': uri}}
        scope = _SCOPE_BY_DECISION.get(decision_norm)
        if not scope:
            raise ValueError('无效的权限选择')
        return {'filePermission': {'allow': True, 'scope': scope, 'absolutePathUri': uri}}
    if kind == 'permission':
        if is_decline:
            return {'permission': {'allow': False, 'scope': 'PERMISSION_SCOPE_ONCE'}}
        scope = _SCOPE_BY_DECISION.get(decision_norm)
        if not scope:
            raise ValueError('无效的权限选择')
        return {'permission': {'allow': True, 'scope': scope}}
    if kind == 'elicitation':
        if is_decline:
            return {'elicitation': {'action': 'decline'}}
        if meta.get('elicitationUnsupported'):
            raise ValueError('此表单请在原客户端完成')
        if decision_norm not in _ACCEPT_DECISIONS:
            raise ValueError('请选择允许或拒绝')
        prop_specs = meta.get('elicitationProps')
        if prop_specs:
            if not isinstance(answers, dict):
                raise ValueError('请回答每一项问题')
            payload = {}
            for prop_name, prop_spec in prop_specs.items():
                ans_entry = answers.get(prop_name)
                skipped = isinstance(ans_entry, dict) and ans_entry.get('skipped') is True
                raw_vals = ans_entry.get('answers') if isinstance(ans_entry, dict) else None
                if skipped or not raw_vals:
                    if prop_name in meta.get('elicitationRequired', []):
                        raise ValueError('请回答每一项必填问题')
                    continue
                payload[prop_name] = _coerce_elicitation_value(raw_vals, prop_spec)
            return {'elicitation': {'action': 'accept', 'contentJson': json.dumps(payload, ensure_ascii=False, allow_nan=False)}}
        if decision_norm not in _ACCEPT_DECISIONS:
            raise ValueError('请选择允许或拒绝')
        return {'elicitation': {'action': 'accept'}}
    if kind == 'deploy':
        if is_decline:
            return {'deploy': {'cancel': True}}
        targets = meta.get('deployTargets') or []
        selected_target = None
        if decision_norm.startswith('deploy_') and decision_norm[7:].isdigit():
            idx = int(decision_norm[7:])
            if 0 <= idx < len(targets):
                selected_target = targets[idx]
        elif decision_norm in _ACCEPT_DECISIONS and targets:
            selected_target = targets[0]
        body = {'cancel': False}
        if selected_target is not None:
            body['deployTarget'] = selected_target
        return {'deploy': body}
    if kind in _CONFIRM_INTERACTION_LABELS:
        if decision_norm not in _ACCEPT_DECISIONS and not is_decline:
            raise ValueError('请选择允许或拒绝')
        return {kind: {'confirm': not is_decline}}
    raise ValueError(f'暂不支持的互动请求类型: {kind}')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeUnavailable('Antigravity 本机服务不允许重定向')


_EFFORT_ORDER = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
_EFFORT_RANK = {name: idx for idx, name in enumerate(_EFFORT_ORDER)}
_EFFORT_LABEL_RE = re.compile(
    r'^(?P<base>.+?)\s*\((?P<effort>' + '|'.join(_EFFORT_ORDER) + r')\)$',
    re.IGNORECASE,
)


def _explicit_native_model(model):
    """提取并规范化用户显式输入的原生 MODEL_ 枚举标识。"""
    if not isinstance(model, str):
        return ''
    stripped = model.strip()
    return stripped.upper() if re.fullmatch(r'MODEL_[A-Z0-9_]+', stripped, re.IGNORECASE | re.ASCII) else ''


def _split_model_variant(model_id, label):
    """Extract (base_id, base_name, effort) when a model label carries an effort suffix."""
    text = str(label or '').strip()
    match = _EFFORT_LABEL_RE.match(text)
    if not match:
        return model_id, (text or model_id), ''
    base_name = match.group('base').strip()
    effort = match.group('effort').lower()
    if not base_name:
        return model_id, (text or model_id), ''
    stripped_id = re.sub(rf'[-_]{re.escape(effort)}$', '', model_id, flags=re.IGNORECASE).strip('-_')
    if stripped_id and stripped_id != model_id:
        return stripped_id, base_name, effort
    slug = re.sub(r'[^a-z0-9.]+', '-', base_name.lower()).strip('-')
    return (slug or model_id), base_name, effort


class AntigravityCli:
    _ALLOWED_METHODS = (
        'GetUserStatus',
        'RetrieveUserQuotaSummary',
        'GetAllCascadeTrajectories',
        'GetCascadeTrajectory',
        'GetCascadeTrajectoryGeneratorMetadata',
        'GetSlashCommands',
        'StartCascade',
        'SendUserCascadeMessage',
        'CancelCascadeInvocation',
        'RevertToCascadeStep',
        'ForceStopCascadeTree',
        'HandleCascadeUserInteraction',
        'UpdateConversationAnnotations',
        'ForkConversation',
        'ListBackgroundTasks',
        'CancelCascadeSteps',
    )

    def __init__(self, home=None, model_catalog=None):
        self.home = Path(home or Path.home()).resolve()
        self.model_catalog = model_catalog
        self._lock = threading.RLock()
        self._stop_lock = threading.RLock()
        self._turn_lock = threading.Lock()
        self._stop_generations = {}
        self._stop_generation = 0
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self._trajectories_cache = {}
        self._annotations_cache = {}
        self._context_window_cache = {}
        self._waiting_cache = {}
        self._variant_cache = {}
        self._base_cache = {}
        self._quota_cache = {}

    def available(self, surface='app'):
        try:
            endpoint(surface, self.home)
            return True
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def source_identity(self, surface='app'):
        try:
            target = endpoint(surface, self.home)
            verify_endpoint(target)
            return target['identity']
        except (OSError, ValueError, subprocess.SubprocessError):
            raise BridgeUnavailable('无法核验 Antigravity 原生服务身份，请重新连接') from None

    def call_ls_rpc(self, method, payload=None, *, surface='app', expected_identity=None, return_identity=False):
        if method not in self._ALLOWED_METHODS:
            raise ValueError('不支持的 Antigravity 原生操作')
        # Native cancellation must reach the LS while SendUserCascadeMessage is
        # still waiting for its response. Other writes retain their serialization.
        with self._stop_lock if method in ('CancelCascadeInvocation', 'ForceStopCascadeTree', 'CancelCascadeSteps',
                                           'ListBackgroundTasks', 'GetAllCascadeTrajectories') else self._lock:
            try:
                target = endpoint(surface, self.home)
                if expected_identity is not None and target['identity'] != expected_identity:
                    raise ValueError('原生服务进程已变化')
                verify_endpoint(target)
                request = urllib.request.Request(
                    target['address'] + '/exa.language_server_pb.LanguageServerService/' + method,
                    data=json.dumps(payload or {}).encode('utf-8'),
                    headers={'Content-Type': 'application/json', 'x-codeium-csrf-token': target['token']})
                timeout = 30 if method in ('StartCascade', 'SendUserCascadeMessage') else 8
                with self._opener.open(request, timeout=timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Antigravity 原生回复过大')
                if method == 'GetAllCascadeTrajectories' and not raw:
                    raise ValueError('Antigravity 任务状态回复为空')
                result = json.loads(raw) if raw else {}
                if not isinstance(result, dict):
                    raise ValueError('Antigravity 原生回复无效')
                verify_endpoint(target)
                return (result, target['identity']) if return_identity else result
            except urllib.error.HTTPError as exc:
                logging.getLogger(__name__).warning('Antigravity RPC %s failed: HTTP %s', method, exc.code)
                if method == 'ForkConversation':
                    try:
                        detail = exc.read(4096).decode('utf-8', errors='replace').lower()
                    except OSError:
                        detail = ''
                    if 'invoked subagents' in detail:
                        raise ResumePreflightBlocked('该对话已调用子代理，Antigravity 原生暂不支持从此处分支') from None
                    if 'cannot fork a subagent' in detail:
                        raise ResumePreflightBlocked('不支持对子代理会话创建分支') from None
                    if 'end of a turn' in detail:
                        raise ResumePreflightBlocked('只能从已完成回复的轮次末尾创建分支') from None
                # Do not expose native error bodies, tokens or retry a write.
                raise BridgeUnavailable('Antigravity 原生操作未确认，请检查电脑原会话后再操作') from None
            except (OSError, ValueError, urllib.error.URLError) as exc:
                logging.getLogger(__name__).warning('Antigravity RPC %s failed: %s', method, type(exc).__name__)
                # Do not expose native error bodies, tokens or retry a write.
                raise BridgeUnavailable('Antigravity 原生操作未确认，请检查电脑原会话后再操作') from None

    def _invalidate_trajectories_cache(self, surface=None):
        with self._lock:
            if surface is None:
                self._trajectories_cache.clear()
                self._annotations_cache.clear()
                self._context_window_cache.clear()
            else:
                self._trajectories_cache.pop(surface, None)
                self._annotations_cache.pop(surface, None)
                for key in [k for k in self._context_window_cache if k[0] == surface]:
                    self._context_window_cache.pop(key, None)

    def _collect_step_fallback(self, cascade_id, step_index, *, surface='app'):
        try:
            res = self.call_ls_rpc('GetCascadeTrajectory', {'cascadeId': cascade_id}, surface=surface)
        except BridgeUnavailable:
            return None
        steps = ((res.get('trajectory') or {}).get('steps')) if isinstance(res.get('trajectory'), dict) else None
        if isinstance(steps, list) and 0 <= step_index < len(steps) and isinstance(steps[step_index], dict):
            return steps[step_index]
        return None

    @staticmethod
    def _coerce_nonneg_int(value):
        if type(value) is int and value >= 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    def context_window_metadata(self, conversation_id, *, surface='app', step_count=None, max_age=15.0):
        """Read latest native context window size (and estimated tokens) from generator metadata."""
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            return None
        cache_key = (surface, conversation_id)
        now = time.monotonic()
        with self._lock:
            cached = self._context_window_cache.get(cache_key)
            if max_age > 0 and cached and cached[1] == step_count and (now - cached[0]) < max_age:
                return dict(cached[2]) if isinstance(cached[2], dict) else None
        res = self.call_ls_rpc(
            'GetCascadeTrajectoryGeneratorMetadata',
            {'cascadeId': conversation_id},
            surface=surface,
        )
        entries = res.get('generatorMetadata') or res.get('generator_metadata')
        found = None
        if isinstance(entries, list):
            for entry in reversed(entries):
                if not isinstance(entry, dict):
                    continue
                chat_model = entry.get('chatModel') or entry.get('chat_model')
                if not isinstance(chat_model, dict):
                    continue
                start_meta = chat_model.get('chatStartMetadata') or chat_model.get('chat_start_metadata')
                if not isinstance(start_meta, dict):
                    continue
                cwm = start_meta.get('contextWindowMetadata') or start_meta.get('context_window_metadata')
                if not isinstance(cwm, dict):
                    continue
                max_tokens = self._coerce_nonneg_int(cwm.get('maxContextTokens', cwm.get('max_context_tokens')))
                if not max_tokens or max_tokens <= 0:
                    continue
                used = self._coerce_nonneg_int(cwm.get('estimatedTokensUsed', cwm.get('estimated_tokens_used')))
                found = {'contextWindow': max_tokens}
                if used is not None:
                    found['estimatedTokensUsed'] = used
                break
        with self._lock:
            self._context_window_cache[cache_key] = (time.monotonic(), step_count, dict(found) if found else None)
            if len(self._context_window_cache) > 128:
                for stale in list(self._context_window_cache.keys())[:-64]:
                    if stale != cache_key:
                        self._context_window_cache.pop(stale, None)
        return found

    def waiting_state_by_cid(self, *, surface='app', max_age=2.0, expected_identity=None):
        """Return (waiting_requests_by_cid, active_cids) for a surface."""
        with self._lock:
            now = time.monotonic()
            with self._lock:
                cached = self._trajectories_cache.get(surface)
                if expected_identity is None and max_age > 0 and cached and (now - cached[0]) < max_age:
                    return {k: [dict(r) for r in v] for k, v in cached[1].items()}, set(cached[2])

            res = self.call_ls_rpc('GetAllCascadeTrajectories', {}, surface=surface,
                                   **({'expected_identity': expected_identity} if expected_identity is not None else {}))
            summaries = res.get('trajectorySummaries', {})
            if not isinstance(summaries, dict):
                raise BridgeUnavailable('原会话等待状态未确认，请稍后重试')

            waiting_by_cid = {}
            active_cids = set()
            annotations_by_cid = {}
            fresh_cache = {}
            for traj_cid, summary in summaries.items():
                if not isinstance(summary, dict):
                    continue
                raw_ann = summary.get('annotations')
                if isinstance(raw_ann, dict):
                    ann = {}
                    if isinstance(raw_ann.get('title'), str) and raw_ann['title'].strip():
                        ann['title'] = raw_ann['title'].strip()
                    if isinstance(raw_ann.get('archived'), bool):
                        ann['archived'] = raw_ann['archived']
                    if ann:
                        annotations_by_cid[traj_cid] = ann
                status = str(summary.get('status') or '')
                meta = summary.get('trajectoryMetadata') if isinstance(summary.get('trajectoryMetadata'), dict) else {}
                root_cid = str(meta.get('rootCascadeId') or meta.get('rootConversationId') or '').strip()
                parent_cid = str(meta.get('parentCascadeId') or meta.get('parentConversationId') or '').strip()
                target_cids = [traj_cid]
                for ancestor in (root_cid, parent_cid):
                    if ancestor and ancestor not in target_cids:
                        target_cids.append(ancestor)
                if (status in _ACTIVE_RUN_STATUSES
                        or any(summary.get(k) is True for k in ('notFullyIdle', 'hasActiveChildren', 'hasRunningSubagents'))
                        or (isinstance(summary.get('backgroundTaskCount'), (int, float)) and summary['backgroundTaskCount'] > 0)
                        or any(isinstance(summary.get(k), list) and summary.get(k) for k in ('waitingSteps', 'subagentWaitingSteps'))):
                    active_cids.update(target_cids)

                sources = []
                for entry in (summary.get('waitingSteps') or []):
                    if isinstance(entry, dict):
                        sources.append((entry, traj_cid, entry.get('trajectoryId') or summary.get('trajectoryId'), None))
                for entry in (summary.get('subagentWaitingSteps') or []):
                    if isinstance(entry, dict):
                        sub_cid = str(entry.get('subagentCascadeId') or entry.get('subagentConversationId') or '').strip()
                        sources.append((entry, sub_cid, entry.get('subagentTrajectoryId'), entry.get('stepIndex')))

                for entry, cascade_id, trajectory_id, explicit_step_idx in sources:
                    step = entry.get('step') if isinstance(entry.get('step'), dict) else {}
                    step_index = explicit_step_idx if explicit_step_idx is not None else entry.get('stepIndex')
                    if not cascade_id or not trajectory_id or step_index is None:
                        continue
                    req_id = make_waiting_request_id(surface, cascade_id, trajectory_id, step_index)
                    public, item_meta = _normalize_waiting_step(
                        req_id, surface, cascade_id, trajectory_id, step_index, step,
                        root_cid=root_cid or traj_cid, parent_cid=parent_cid,
                    )
                    if public is None:
                        full_step = self._collect_step_fallback(cascade_id, int(step_index), surface=surface)
                        if isinstance(full_step, dict):
                            public, item_meta = _normalize_waiting_step(
                                req_id, surface, cascade_id, trajectory_id, step_index, full_step,
                                root_cid=root_cid or traj_cid, parent_cid=parent_cid,
                            )
                    if public is None or item_meta is None:
                        continue
                    item_meta['ownerCids'] = sorted(set(target_cids + [cascade_id])
                        | set(fresh_cache.get(req_id, {}).get('ownerCids', [])))
                    fresh_cache[req_id] = item_meta
                    for owner in target_cids:
                        requests = waiting_by_cid.setdefault(owner, [])
                        if not any(item['id'] == req_id for item in requests):
                            requests.append(dict(public))

            with self._lock:
                self._trajectories_cache[surface] = (time.monotonic(), {k: [dict(r) for r in v] for k, v in waiting_by_cid.items()}, set(active_cids))
                self._annotations_cache[surface] = {k: dict(v) for k, v in annotations_by_cid.items()}
                self._waiting_cache = {key: value for key, value in self._waiting_cache.items() if value.get('surface') != surface}
                self._waiting_cache.update(fresh_cache)
                if len(self._waiting_cache) > 256:
                    for stale_key in list(self._waiting_cache.keys())[:-128]:
                        if stale_key not in fresh_cache:
                            self._waiting_cache.pop(stale_key, None)

            return waiting_by_cid, active_cids

    def annotations_for(self, conversation_id, *, surface='app', fresh=False):
        """Return cached or freshly read native annotations (title/archived) for a conversation."""
        if not conversation_id:
            return {}
        if fresh:
            self.waiting_state_by_cid(surface=surface, max_age=0.0)
        with self._lock:
            return dict((self._annotations_cache.get(surface) or {}).get(conversation_id) or {})

    def update_annotations(self, conversation_id, *, title=None, archived=None, surface='app', expected_identity=None):
        """Update native conversation annotations via UpdateConversationAnnotations (merge)."""
        annotations = {}
        if title is not None:
            annotations['title'] = title
        if archived is not None:
            annotations['archived'] = bool(archived)
        if not annotations:
            raise ValueError('缺少要更新的会话标注字段')
        result = self.call_ls_rpc(
            'UpdateConversationAnnotations',
            {
                'cascadeId': conversation_id,
                'annotations': annotations,
                'mergeAnnotations': True,
            },
            surface=surface,
            **({'expected_identity': expected_identity} if expected_identity is not None else {}),
        )
        self._invalidate_trajectories_cache(surface)
        return result

    def has_waiting_interactions(self, conversation_id, *, surface='app'):
        if not conversation_id:
            return False
        waiting_by_cid, _ = self.waiting_state_by_cid(surface=surface)
        return bool(waiting_by_cid.get(conversation_id))

    def _resolve_waiting_meta(self, expected_cid, request_id, *, surface='app'):
        parsed = parse_waiting_request_id(request_id)
        if not parsed or (parsed[0] and parsed[0] != surface):
            raise ValueError('该互动请求来源不匹配')
        # Display caches cannot authorize a response: a question may have been
        # answered, or moved to another parent, since it was shown.
        self.waiting_state_by_cid(surface=surface, max_age=0.0)
        with self._lock:
            meta = self._waiting_cache.get(request_id)
        if meta is None:
            raise ValueError('该互动请求已过期或已在桌面端处理')
        if meta.get('surface') != surface:
            raise ValueError('该互动请求来源不匹配')
        owners = set(meta.get('ownerCids', []))
        if expected_cid and expected_cid not in owners:
            raise ValueError('该请求不属于当前会话')
        return meta

    def respond_interaction(self, conversation_id, request_id, decision, answers=None, *, surface='app'):
        """Submit user answers or approval decisions via HandleCascadeUserInteraction."""
        with self._lock:
            meta = self._resolve_waiting_meta(conversation_id, request_id, surface=surface)
            interaction_field = _build_interaction_response(meta, decision, answers=answers)
            payload = {
                'cascadeId': meta['cascadeId'],
                'interaction': {
                    'trajectoryId': meta['trajectoryId'],
                    'stepIndex': meta['stepIndex'],
                    **interaction_field,
                },
            }
            self.call_ls_rpc('HandleCascadeUserInteraction', payload, surface=surface)
            self._invalidate_trajectories_cache(surface)
            with self._lock:
                self._waiting_cache.pop(request_id, None)
            return True

    def user_status(self, surface='app'):
        return self.call_ls_rpc('GetUserStatus', surface=surface).get('userStatus') or {}

    def quota_snapshot(self, surface='app', *, refresh=False):
        """Bounded, account/process-scoped display reads; never used to authorize writes."""
        def scope():
            identity = self.source_identity(surface)
            user = self.user_status(surface)
            account = user.get('email') or user.get('userId')
            if not isinstance(account, str) or not account:
                raise BridgeUnavailable('Antigravity 账号状态暂不可用')
            return identity, hashlib.sha256(account.encode()).hexdigest()

        with self._lock:
            try:
                identity = scope()
            except (OSError, ValueError, BridgeUnavailable):
                self._quota_cache.pop(surface, None)
                return {'groups': [], 'status': 'unavailable'}
            cached = self._quota_cache.get(surface)
            if cached and cached[0] != identity:
                self._quota_cache.pop(surface, None)
                cached = None
            if cached and not refresh and time.monotonic() - cached[1] < 60:
                return copy.deepcopy(cached[2])
            try:
                response = self.call_ls_rpc('RetrieveUserQuotaSummary', {'forceRefresh': refresh},
                                            surface=surface, expected_identity=identity[0])
                raw = response.get('response')
                if not isinstance(raw, dict) or not isinstance(raw.get('groups'), list):
                    raise ValueError('额度回复无效')
                value = {'groups': quota_groups(raw), 'status': 'ready', 'checkedAt': time.time()}
            except (OSError, ValueError, BridgeUnavailable):
                value = {**copy.deepcopy(cached[2]), 'status': 'stale'} if cached and cached[2].get('groups') else {'groups': [], 'status': 'unavailable'}
            try:
                if scope() != identity:
                    raise BridgeUnavailable('Antigravity 账号或进程已变化')
            except (OSError, ValueError, BridgeUnavailable):
                self._quota_cache.pop(surface, None)
                return {'groups': [], 'status': 'unavailable'}
            self._quota_cache[surface] = (identity, time.monotonic(), value)
            return copy.deepcopy(value)

    def current_model_quota(self, surface, model_id, model_name=''):
        snapshot = self.quota_snapshot(surface)
        return {'quotaWindows': model_windows(snapshot['groups'], model_id, model_name),
                'quotaStatus': snapshot['status'], 'quotaUpdatedAt': snapshot.get('checkedAt')}

    def overlay_live_quota(self, surface, catalog):
        if surface is not None:
            return overlay_quota(catalog, self.quota_snapshot(surface))
        value = copy.deepcopy(catalog)
        value['groups'] = [overlay_quota(group, self.quota_snapshot(group['surface']))
                           if group.get('surface') in SURFACE_LABELS else group
                           for group in value.get('groups', [])]
        return value

    def assert_idle(self, surface, expected_identity):
        # Request every trajectory, including children. Saved history is neither
        # complete nor synchronous enough to authorize an account restart.
        result = self.call_ls_rpc('GetAllCascadeTrajectories', {'exclude_subtrajectories': False},
                                  surface=surface, expected_identity=expected_identity)
        # The installed protobuf service omits an empty map: a successful,
        # identity-verified response {} is its real zero-trajectory response.
        # Do not extend this to malformed fields, error objects or DB fallbacks.
        rows = {} if result == {} else result.get('trajectorySummaries')
        if set(result) - {'trajectorySummaries'}:
            raise ValueError('无法完整核验 Antigravity 任务，请在原客户端正常退出后切换账号')
        if not isinstance(rows, dict):
            raise ValueError('无法完整核验 Antigravity 任务，请在原客户端正常退出后切换账号')
        for row in rows.values():
            if not isinstance(row, dict) or row.get('status') != 'CASCADE_RUN_STATUS_IDLE':
                raise ValueError('Antigravity 有未结束或状态未知的任务，请处理后重试')
            if any(row.get(key) for key in ('waitingSteps', 'notFullyIdle', 'hasActiveChildren',
                                           'hasRunningSubagents', 'backgroundTaskCount')):
                raise ValueError('Antigravity 有运行任务、后台任务或待确认操作，请处理后重试')

    @staticmethod
    def _format_reset_note(reset_time):
        text = str(reset_time or '').strip()
        if not text:
            return ''
        stamp = _parse_iso_seconds(text)
        if stamp is not None:
            return f'将于 {datetime.fromtimestamp(stamp).astimezone():%m/%d %H:%M %Z} 重置'
        return f'重置时间 {text}'

    @staticmethod
    def _quota_info(row):
        q = row.get('quotaInfo')
        if not isinstance(q, dict):
            return None
        frac = q.get('remainingFraction', 0)  # Present protobuf message; omitted scalar means zero.
        reset_note = AntigravityCli._format_reset_note(q.get('resetTime'))
        if not isinstance(frac, (int, float)):
            return ({'quotaTag': '', 'quotaStatus': '', 'resetNote': reset_note} if reset_note else None)
        if frac <= 0.0:
            return {'quotaTag': '已用尽', 'quotaStatus': 'exhausted', 'resetNote': reset_note}
        if frac <= 0.005:
            return {'quotaTag': f'剩余 {frac * 100.0:.1f}%', 'quotaStatus': 'exhausted', 'resetNote': reset_note}
        if frac >= 0.999:
            return {'quotaTag': '剩余 100%', 'quotaStatus': 'good', 'resetNote': ''}
        return {
            'quotaTag': f'剩余 {frac * 100.0:.1f}%',
            'quotaStatus': 'warning' if frac < 0.2 else 'good',
            'resetNote': reset_note,
        }

    def _build_catalog_from_status(self, status, *, surface='app'):
        model_data = status.get('cascadeModelConfigData') if isinstance(status.get('cascadeModelConfigData'), dict) else {}
        configs = model_data.get('clientModelConfigs') if isinstance(model_data.get('clientModelConfigs'), list) else []
        default_override = model_data.get('defaultOverrideModelConfig') if isinstance(model_data.get('defaultOverrideModelConfig'), dict) else {}
        default_alias = default_override.get('modelOrAlias') if isinstance(default_override.get('modelOrAlias'), dict) else {}
        default_override_enum = str(default_alias.get('model') or '').strip()

        user_tier = status.get('userTier') if isinstance(status.get('userTier'), dict) else {}
        tier_name = str(user_tier.get('name') or '').strip()
        plan_status = status.get('planStatus') if isinstance(status.get('planStatus'), dict) else {}

        # Detect ungrouped IDs first so grouped base IDs never collide with standalone rows.
        ungrouped_ids = set()
        parsed_rows = []
        variant_ids = {}
        for row in configs:
            if not self._is_enabled_model_row(row):
                continue
            mid = row['modelId']
            raw_label = row.get('label') or mid
            base_id, base_name, effort = _split_model_variant(mid, raw_label)
            if not effort:
                ungrouped_ids.add(mid)
            else:
                variant = (mid, row['modelOrAlias']['model'])
                previous = variant_ids.setdefault((base_id, effort), variant)
                if previous != variant:
                    # A display label is not an identity. Keep ambiguous native
                    # variants selectable rather than overwriting one model.
                    ungrouped_ids.add(base_id)
            parsed_rows.append((row, mid, raw_label, base_id, base_name, effort))

        groups = {}
        ordered_keys = []
        for row, mid, raw_label, base_id, base_name, effort in parsed_rows:
            alias = row['modelOrAlias']
            native_model = alias['model']
            quota = self._quota_info(row)
            q_raw = row.get('quotaInfo') if isinstance(row.get('quotaInfo'), dict) else {}
            frac = q_raw.get('remainingFraction', 0) if q_raw else None
            if effort and base_id not in ungrouped_ids:
                key = ('grouped', base_id)
                bucket = groups.get(key)
                if bucket is None:
                    bucket = {
                        'grouped': True,
                        'id': base_id,
                        'name': base_name,
                        'variants': {},
                        'quotas': [],
                    }
                    groups[key] = bucket
                    ordered_keys.append(key)
                bucket['variants'][effort] = {'id': mid, 'nativeModel': native_model, 'label': raw_label}
                if quota:
                    bucket['quotas'].append((frac if isinstance(frac, (int, float)) else 2.0, quota))
            else:
                key = ('flat', mid)
                groups[key] = {
                    'grouped': False,
                    'id': mid,
                    'name': raw_label,
                    'nativeModel': native_model,
                    'quotas': [(frac if isinstance(frac, (int, float)) else 2.0, quota)] if quota else [],
                }
                ordered_keys.append(key)

        models = []
        usage_items = []
        variant_cache = {}
        base_cache = {}

        for key in ordered_keys:
            bucket = groups[key]
            if bucket['grouped']:
                variants = bucket['variants']
                efforts = sorted(variants.keys(), key=lambda item: _EFFORT_RANK.get(item, len(_EFFORT_ORDER)))
                ordered_variants = {eff: variants[eff] for eff in efforts}
                default_effort = next(
                    (eff for eff in efforts if ordered_variants[eff]['nativeModel'] == default_override_enum),
                    'medium' if 'medium' in ordered_variants else efforts[0],
                )
                entry = {
                    'id': bucket['id'],
                    'name': bucket['name'],
                    'efforts': efforts,
                    'supportsEffort': True,
                    'defaultEffort': default_effort,
                    'nativeModel': ordered_variants[default_effort]['nativeModel'],
                    'variants': ordered_variants,
                }
                for eff, variant in ordered_variants.items():
                    variant_cache[variant['id']] = (bucket['id'], eff, bucket['name'])
                base_cache[bucket['id']] = {
                    'name': bucket['name'],
                    'efforts': efforts,
                    'defaultEffort': default_effort,
                }
            else:
                entry = {
                    'id': bucket['id'],
                    'name': bucket['name'],
                    'efforts': [],
                    'supportsEffort': False,
                    'nativeModel': bucket['nativeModel'],
                }
                base_cache[bucket['id']] = {'name': bucket['name'], 'efforts': [], 'defaultEffort': ''}

            if bucket['quotas']:
                _, quota = min(bucket['quotas'], key=lambda pair: pair[0])
                entry.update({k: v for k, v in quota.items() if v})
                if quota['quotaTag']:
                    usage_items.append({
                        'name': entry['name'],
                        'val': quota['quotaTag'],
                        'note': quota['resetNote'],
                        'status': quota['quotaStatus'],
                    })
            models.append(entry)

        with self._lock:
            self._variant_cache[surface] = variant_cache
            self._base_cache[surface] = base_cache

        p_credits = plan_status.get('availablePromptCredits', 0)
        f_credits = plan_status.get('availableFlowCredits', 0)
        credits_line = ''
        if isinstance(status.get('planStatus'), dict):
            credits_line = f'可用 Prompt 点数: {p_credits or 0} · Flow 点数: {f_credits or 0}'
        notice = '' if models else '原客户端暂未提供可用模型列表'
        result = {'models': models, 'userStatus': status, 'notice': notice}
        if usage_items or credits_line:
            result['usageSummary'] = {'tier': tier_name, 'items': usage_items, 'credits': credits_line}
        return result


    @staticmethod
    def _resolve_from_catalog(model, effort, catalog, *, require_effort=False):
        rows = catalog.get('models', []) if isinstance(catalog, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            variants = row.get('variants') if isinstance(row.get('variants'), dict) else None
            if row.get('id') == model:
                if variants:
                    if effort:
                        variant = variants.get(effort)
                        if isinstance(variant, dict) and variant.get('nativeModel'):
                            return variant['nativeModel'], row['id'], effort, row.get('name') or row['id']
                        raise ValueError('所选模型不支持此思考强度，请重新选择')
                    if require_effort:
                        raise ValueError('请先选择思考强度')
                    default_eff = row.get('defaultEffort')
                    if default_eff and isinstance(variants.get(default_eff), dict) and variants[default_eff].get('nativeModel'):
                        return variants[default_eff]['nativeModel'], row['id'], default_eff, row.get('name') or row['id']
                    if row.get('nativeModel'):
                        return row['nativeModel'], row['id'], default_eff or '', row.get('name') or row['id']
                else:
                    if effort:
                        raise ValueError('所选模型不支持单独设置思考强度')
                    if row.get('nativeModel'):
                        return row['nativeModel'], row['id'], '', row.get('name') or row['id']
        for row in rows:
            if not isinstance(row, dict):
                continue
            variants = row.get('variants') if isinstance(row.get('variants'), dict) else None
            if not variants:
                continue
            for eff, variant in variants.items():
                if isinstance(variant, dict) and variant.get('id') == model and variant.get('nativeModel'):
                    if not effort or effort in ('auto', eff):
                        return variant['nativeModel'], row['id'], eff, row.get('name') or row['id']
                    raise ValueError('所选模型不支持此思考强度，请重新选择')
        return None, None, '', ''


    def _index_catalog_display(self, surface, catalog):
        variant_cache = {}
        base_cache = {}
        for row in catalog.get('models', []) if isinstance(catalog, dict) else []:
            if not isinstance(row, dict) or not row.get('id'):
                continue
            rid = row['id']
            name = row.get('name') or rid
            efforts = list(row.get('efforts') or [])
            base_cache[rid] = {'name': name, 'efforts': efforts, 'defaultEffort': row.get('defaultEffort') or ''}
            variants = row.get('variants') if isinstance(row.get('variants'), dict) else {}
            for eff, variant in variants.items():
                if isinstance(variant, dict) and variant.get('id'):
                    variant_cache[variant['id']] = (rid, eff, name)
        with self._lock:
            self._variant_cache[surface] = variant_cache
            self._base_cache[surface] = base_cache


    def resolve_model_display(self, surface, model, effort='', *, refresh_if_unknown=False):
        """Map a stored flat or grouped model into (model, effort, displayName)."""
        if not model:
            return '', '', ''
        explicit = _explicit_native_model(model)
        if explicit:
            return explicit, '', explicit
        with self._lock:
            variants = dict(self._variant_cache.get(surface, {}))
            bases = dict(self._base_cache.get(surface, {}))
        if refresh_if_unknown and model not in bases and model not in variants:
            catalog = self.model_catalog(surface) if self.model_catalog else self.catalog_models(surface=surface)
            self._index_catalog_display(surface, catalog)
            with self._lock:
                variants = dict(self._variant_cache.get(surface, {}))
                bases = dict(self._base_cache.get(surface, {}))
        if model in variants and (not effort or effort in ('auto', variants[model][1])):
            base_id, var_eff, base_name = variants[model]
            return base_id, var_eff, base_name
        if model in bases:
            return model, effort, bases[model]['name']
        return model, effort, ''


    def canonicalize_model_selection(self, model, effort=None, *, surface='app', require_effort=False):
        if model is None:
            if effort:
                raise ValueError('请先选择模型后再设置思考强度')
            return None, None, '', ''
        explicit = _explicit_native_model(model)
        if explicit:
            if effort:
                raise ValueError('手动填写原生模型标识不支持单独选择思考强度')
            return explicit, explicit, '', explicit
        if self.model_catalog is not None:
            def missing(cached):
                # Older catalogs could store a variant as an ordinary row.
                # Let the live catalog identify the variant before rejecting auto.
                if effort == 'auto' and any(isinstance(row, dict) and row.get('id') == model
                        and not row.get('variants') for row in cached.get('models', [])):
                    return True
                return not self._resolve_from_catalog(model, effort, cached, require_effort=require_effort)[0]

            catalog = self.model_catalog(surface, refresh_if=missing)
        else:
            catalog = self.catalog_models(surface=surface)
        self._index_catalog_display(surface, catalog)
        native_enum, canonical_model, canonical_effort, display_name = self._resolve_from_catalog(
            model, effort, catalog, require_effort=require_effort,
        )
        if not native_enum:
            raise ValueError('未找到此模型的原生标识，请刷新目录或手动填写完整 MODEL_ 标识')
        return native_enum, canonical_model, canonical_effort, display_name


    def catalog_models(self, *, refresh=False, surface='app'):
        status = self.user_status(surface)
        catalog = self._build_catalog_from_status(status, surface=surface)
        return overlay_quota(catalog, self.quota_snapshot(surface, refresh=refresh)) if catalog['models'] else catalog

    @staticmethod
    def _is_enabled_model_row(row):
        if not isinstance(row, dict):
            return False
        mid = row.get('modelId')
        if not isinstance(mid, str) or not mid:
            return False
        if row.get('disabled') or row.get('tagTitle') == 'Unavailable' or mid.endswith('-disabled'):
            return False
        alias = row.get('modelOrAlias')
        return isinstance(alias, dict) and bool(alias.get('model'))

    def _model(self, model, surface, effort=None, *, require_effort=False):
        if model is None:
            if effort:
                raise ValueError('请先选择模型后再设置思考强度')
            return None  # Preserve the original server's selected default.
        explicit = _explicit_native_model(model)
        if explicit:
            if effort:
                raise ValueError('手动填写原生模型标识不支持单独选择思考强度')
            return explicit  # Explicit native enum, validated by the original service.
        native_enum, _, _, _ = self.canonicalize_model_selection(
            model, effort, surface=surface, require_effort=require_effort,
        )
        return native_enum

    def _raw_model_for_commands(self, model=None, surface='app', effort=None):
        if model and model != 'auto':
            return self._model(model, surface, effort=effort)
        if effort:
            raise ValueError('请先选择模型后再设置思考强度')
        status = self.user_status(surface)
        model_data = status.get('cascadeModelConfigData') if isinstance(status.get('cascadeModelConfigData'), dict) else {}
        configs = model_data.get('clientModelConfigs') if isinstance(model_data.get('clientModelConfigs'), list) else []
        default_cfg = model_data.get('defaultOverrideModelConfig')
        if isinstance(default_cfg, dict):
            alias = default_cfg.get('modelOrAlias')
            if isinstance(alias, dict) and alias.get('model'):
                return alias['model']
        for row in configs:
            if self._is_enabled_model_row(row):
                return row['modelOrAlias']['model']
        return None

    def slash_commands(self, *, surface='app', conversation_id=None, model=None, effort=None, cwd=None):
        payload = {'enablePersonal': True}
        if isinstance(conversation_id, str) and conversation_id.strip():
            payload['cascadeId'] = conversation_id.strip()
        if isinstance(cwd, str) and cwd.strip():
            try:
                path = Path(cwd).resolve()
                if path.is_dir():
                    payload['workspaceUris'] = [path.as_uri()]
            except (OSError, ValueError):
                pass
        raw_model = self._raw_model_for_commands(model, surface, effort)
        if raw_model:
            payload['cascadeConfig'] = {'plannerConfig': {'requestedModel': {'model': raw_model}}}
        res = self.call_ls_rpc('GetSlashCommands', payload, surface=surface)
        raw_commands = res.get('commands') if isinstance(res.get('commands'), list) else []
        commands = []
        seen = set()
        for row in raw_commands:
            if not isinstance(row, dict):
                continue
            info = row.get('info')
            if not isinstance(info, dict):
                continue
            name = str(info.get('name') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            cmd_type = str(info.get('type') or '')
            if cmd_type == 'SLASH_COMMAND_TYPE_SYSTEM':
                category = 'system'
            elif cmd_type == 'SLASH_COMMAND_TYPE_SKILL':
                category = 'skill'
            else:
                category = 'workflow'
            title = str(row.get('title') or name).strip() or name
            description = str(row.get('description') or '').strip()
            commands.append({
                'id': name,
                'name': name,
                'title': title,
                'description': description,
                'category': category,
                'type': cmd_type,
                'info': dict(info),
            })
        return commands

    def turn_generation(self, conversation_id=None, *, surface='app'):
        with self._turn_lock:
            if conversation_id is None:
                return self._stop_generation
            return self._stop_generations.get((surface, conversation_id), 0)

    @staticmethod
    def _workspace_uri_matches(value, expected_path, expected_uri):
        if not isinstance(value, str):
            return False
        if value == expected_uri:
            return True
        try:
            local = Path(local_file_path(value))
            return local.is_absolute() and local.resolve() == expected_path
        except (OSError, RuntimeError, ValueError):
            return False

    @classmethod
    def _single_workspace_matches(cls, uris, path, uri):
        return (isinstance(uris, list) and len(uris) == 1
                and cls._workspace_uri_matches(uris[0], path, uri))

    @classmethod
    def _workspace_declarations_match(cls, metadata, expected_path, expected_uri, *, include_workspaces=False):
        if not isinstance(metadata, dict):
            return False
        declarations = [metadata[key] for key in ('workspaceUris', 'workspace_uris') if key in metadata]
        if include_workspaces and 'workspaces' in metadata:
            workspaces = metadata['workspaces']
            if not isinstance(workspaces, list) or len(workspaces) != 1 or not isinstance(workspaces[0], dict):
                return False
            workspace = workspaces[0]
            workspace_uris = [[workspace[key]] for key in ('workspaceFolderAbsoluteUri', 'workspace_folder_absolute_uri')
                              if key in workspace]
            if not workspace_uris:
                return False
            declarations.extend(workspace_uris)
        return bool(declarations) and all(
            cls._single_workspace_matches(uris, expected_path, expected_uri) for uris in declarations
        )

    def _summary_db_workspace(self, surface_root, cascade_id, path, uri):
        """Return True for a match, False for a contradiction, None without DB evidence."""
        db_path = surface_root / 'conversation_summaries.db'
        try:
            # A linked surface or DB must not supply evidence for a different profile.
            if db_path.resolve() != db_path or not db_path.is_file():
                return None
            connection = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=1.5)
            try:
                rows = connection.execute(
                    'SELECT workspace_uris FROM conversation_summaries WHERE conversation_id = ?',
                    (cascade_id,),
                ).fetchmany(2)
            finally:
                connection.close()
        except (OSError, RuntimeError, sqlite3.Error, ValueError):
            return None
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0][0], str):
            return False
        try:
            return self._single_workspace_matches(json.loads(rows[0][0]), path, uri)
        except (ValueError, RecursionError):
            return False

    def _verify_cascade_workspace(self, result, cascade_id, path, uri, surface, expected_identity):
        # Explicit native project data is authoritative, including malformed or empty values.
        environment_keys = [key for key in ('projectEnvInfo', 'project_env_info') if key in result]
        if environment_keys:
            return all(self._workspace_declarations_match(result[key], path, uri)
                       for key in environment_keys)

        relative = dict(SURFACE_DIRS).get(surface)
        if relative is None:
            return False
        db_match = self._summary_db_workspace(self.home / relative, cascade_id, path, uri)
        if db_match is not None:
            return db_match

        trajectory = self.call_ls_rpc('GetCascadeTrajectory', {'cascadeId': cascade_id}, surface=surface,
                                      expected_identity=expected_identity)
        native_trajectory = trajectory.get('trajectory')
        metadata = native_trajectory.get('metadata') if isinstance(native_trajectory, dict) else None
        for envelope in (trajectory, native_trajectory, metadata):
            if isinstance(envelope, dict) and any(
                envelope[key] != cascade_id for key in ('cascadeId', 'cascade_id', 'conversationId', 'conversation_id')
                if key in envelope
            ):
                return False
        return self._workspace_declarations_match(metadata, path, uri, include_workspaces=True)

    def send_user_message(self, conversation_id, prompt, model=None, effort=None, *, surface='app', items=None,
                          media=None, turn_generation=None, before_send=None, expected_identity=None):
        if turn_generation is None:
            turn_generation = self.turn_generation(conversation_id, surface=surface)
        normalized_media = [dict(row) for row in (media or []) if isinstance(row, dict)]
        if items is not None:
            normalized_items = list(items)
        elif prompt or normalized_media:
            normalized_items = [{'text': prompt or ''}]
        else:
            normalized_items = []
        if not normalized_items and not normalized_media:
            raise ValueError('Antigravity 消息内容不能为空')
        if not normalized_items:
            normalized_items = [{'text': ''}]
        body = {'cascade_id': conversation_id, 'items': normalized_items}
        if normalized_media:
            body['media'] = normalized_media
        selected = self._model(model, surface, effort=effort) if effort else self._model(model, surface)
        if selected:
            body['cascade_config'] = {'planner_config': {'requested_model': {'model': selected}}}
        with self._lock:
            with self._turn_lock:
                if turn_generation < self._stop_generations.get((surface, conversation_id), 0):
                    raise BridgeUnavailable('此消息准备期间会话已停止，请明确重新发送')
                if before_send is not None:
                    before_send()
            # No generation lock spans HTTP: native RPC has no atomic cancel/submit token.
            identity = {'expected_identity': expected_identity} if expected_identity is not None else {}
            result = self.call_ls_rpc('SendUserCascadeMessage', body, surface=surface, **identity)
        self._invalidate_trajectories_cache(surface)
        return result

    def start_cascade(self, *, surface='app', model=None, effort=None, cwd=None):
        path = Path(cwd or '').resolve()
        if not cwd or not path.is_dir():
            raise ValueError('请选择有效的本机项目目录')
        uri = path.as_uri()
        body = {'source': 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT', 'workspace_uris': [uri]}
        selected = self._model(model, surface, effort=effort) if effort else self._model(model, surface)
        if selected:
            body['requested_model'] = selected
        result, expected_identity = self.call_ls_rpc('StartCascade', body, surface=surface, return_identity=True)
        cid = result.get('cascadeId') or result.get('cascade_id')
        if (not isinstance(cid, str) or not cid.strip()
                or any(result[key] != cid for key in ('cascadeId', 'cascade_id') if key in result)):
            raise BridgeUnavailable('Antigravity 原客户端未确认新会话标识')
        if not self._verify_cascade_workspace(result, cid, path, uri, surface, expected_identity):
            raise BridgeUnavailable('原客户端尚未确认新会话项目绑定，首条消息未发送；请在电脑检查已创建的会话')
        self._invalidate_trajectories_cache(surface)
        return cid, expected_identity

    def fork_conversation(self, conversation_id, step_index, *, cwd=None, surface='app', expected_identity=None):
        """Fork a conversation at a verified turn-end step into the current workspace."""
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError('源会话标识无效')
        if type(step_index) is not int or step_index < 0:
            raise ValueError('分支消息位置无效')
        path = Path(cwd or '').resolve()
        if not cwd or not path.is_dir():
            raise ResumePreflightBlocked('请选择有效的原会话项目目录后再创建分支')
        uri = path.as_uri()
        if expected_identity is None:
            expected_identity = self.source_identity(surface)
        payload = {
            'sourceCascadeId': conversation_id,
            'forkAtStepIndex': step_index,
            'targetForkWorkspace': 'FORK_TARGET_CURRENT_WORKSPACE',
            'sourceWorkspaceUris': [uri],
        }
        result = self.call_ls_rpc('ForkConversation', payload, surface=surface, expected_identity=expected_identity)
        new_cid = result.get('newCascadeId') or result.get('new_cascade_id')
        if (not isinstance(new_cid, str) or not new_cid.strip() or new_cid == conversation_id
                or any(result[key] != new_cid for key in ('newCascadeId', 'new_cascade_id') if key in result)):
            raise BridgeUnavailable('Antigravity 尚未确认分支结果，请在电脑核对')
        self._invalidate_trajectories_cache(surface)
        if not self._verify_cascade_workspace(result, new_cid, path, uri, surface, expected_identity):
            raise BridgeUnavailable('原客户端尚未确认分支的项目归属，请在电脑核对；不会重复创建分支')
        return new_cid.strip()

    def stop(self, conversation_id, *, surface='app', before_stop=None):
        with self._turn_lock:
            key = (surface, conversation_id)
            self._stop_generation += 1
            self._stop_generations[key] = self._stop_generation
            if before_stop is not None:
                before_stop()
        cancel_result = None
        try:
            try:
                cancel_result = self.call_ls_rpc(
                    'CancelCascadeInvocation',
                    {'cascade_id': conversation_id, 'kill_background_tasks': True},
                    surface=surface,
                )
            except BridgeUnavailable:
                cancel_result = None
            tree_result = self.call_ls_rpc(
                'ForceStopCascadeTree',
                {'conversationId': conversation_id},
                surface=surface,
            )
        finally:
            self._invalidate_trajectories_cache(surface)
        if isinstance(tree_result, dict) and tree_result:
            return {**(cancel_result if isinstance(cancel_result, dict) else {}), **tree_result}
        return cancel_result if isinstance(cancel_result, dict) else (tree_result or {})

    def running_child_cids(self, conversation_id, *, surface='app', expected_identity):
        result = self.call_ls_rpc('GetAllCascadeTrajectories', {'exclude_subtrajectories': False},
                                  surface=surface, expected_identity=expected_identity)
        summaries = result.get('trajectorySummaries', {})
        if not isinstance(summaries, dict):
            raise BridgeUnavailable('原生子任务状态未确认')
        children = set()
        for cid, summary in summaries.items():
            if cid == conversation_id or not isinstance(summary, dict):
                continue
            meta = summary.get('trajectoryMetadata')
            meta = meta if isinstance(meta, dict) else {}
            parents = [item[name] for item in (summary, meta)
                       for name in ('parentCascadeId', 'parentConversationId') if item.get(name)]
            if (parents and all(parent == conversation_id for parent in parents)
                    and (summary.get('status') in _ACTIVE_RUN_STATUSES or summary.get('notFullyIdle') is True)):
                children.add(cid)
        return children

    def list_background_tasks(self, conversation_id, *, surface='app', expected_identity=None):
        """Return live background command tasks belonging exclusively to conversation_id."""
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            return []
        result = self.call_ls_rpc('ListBackgroundTasks', {}, surface=surface,
                                  **({'expected_identity': expected_identity} if expected_identity is not None else {}))
        raw_tasks = result.get('tasks')
        if not isinstance(raw_tasks, list):
            return []
        tasks = []
        seen = set()
        for entry in raw_tasks:
            if not isinstance(entry, dict) or entry.get('conversationId') != conversation_id:
                continue
            task = entry.get('task')
            if not isinstance(task, dict):
                continue
            step_index = task.get('stepIndex')
            has_step = type(step_index) is int and step_index >= 0
            raw_id = str(task.get('taskId') or '').strip()
            task_id = f'bg:{step_index}' if has_step else raw_id
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            label = str(task.get('toolSummary') or task.get('description') or task.get('toolName') or '后台任务').strip()
            if task.get('isDaemon') is True and not label.startswith('[常驻]'):
                label = '[常驻] ' + label
            tasks.append({
                'id': task_id,
                'title': label[:120],
                'status': 'running',
                'cancellable': has_step,
            })
        return tasks

    def stop_background_step(self, conversation_id, step_index, *, surface='app', expected_identity):
        """Cancel a single live background step within conversation_id without stopping the turn."""
        if type(step_index) is not int or step_index < 0:
            raise ValueError('后台任务步骤标识无效')
        try:
            return self.call_ls_rpc(
                'CancelCascadeSteps',
                {'cascade_id': conversation_id, 'step_indices': [step_index]},
                surface=surface,
                expected_identity=expected_identity,
            )
        finally:
            self._invalidate_trajectories_cache(surface)

    def stop_child_cascade(self, child_conversation_id, *, surface='app', before_stop=None, expected_identity):
        """Stop only a verified child subagent cascade tree without touching the parent conversation."""
        with self._turn_lock:
            self._stop_generation += 1
            self._stop_generations[(surface, child_conversation_id)] = self._stop_generation
            if before_stop is not None:
                before_stop()
        cancel_result = None
        tree_result = None
        try:
            try:
                cancel_result = self.call_ls_rpc(
                    'CancelCascadeInvocation',
                    {'cascade_id': child_conversation_id, 'kill_background_tasks': True, 'notify_parent': True},
                    surface=surface,
                    expected_identity=expected_identity,
                )
            except BridgeUnavailable:
                cancel_result = None
            tree_result = self.call_ls_rpc(
                'ForceStopCascadeTree',
                {'conversationId': child_conversation_id},
                surface=surface,
                expected_identity=expected_identity,
            )
        finally:
            self._invalidate_trajectories_cache(surface)
        if isinstance(tree_result, dict) and tree_result:
            return {**(cancel_result if isinstance(cancel_result, dict) else {}), **tree_result}
        return cancel_result if isinstance(cancel_result, dict) else (tree_result or {})

    def revert_to_step(self, conversation_id, step_index, *, conversation_only=True, surface='app', expected_identity=None):
        if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0:
            raise ValueError('撤回步骤位置无效')
        result = self.call_ls_rpc(
            'RevertToCascadeStep',
            {'cascadeId': conversation_id, 'stepIndex': step_index, 'conversationOnly': bool(conversation_only)},
            surface=surface, expected_identity=expected_identity,
        )
        self._invalidate_trajectories_cache(surface)
        return result

    def close(self):
        pass
