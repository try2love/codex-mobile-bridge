"""Use the verified original Antigravity server; never spawn a competing CLI."""
import json
from pathlib import Path
import threading
import subprocess
import time
import urllib.error
import urllib.request

from bridge.clients.errors import BridgeUnavailable
from bridge.platforms.antigravity import endpoint, verify_endpoint
from .model import SURFACE_LABELS, format_tool_call

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
    if not isinstance(schema, dict):
        return [], {}
    props = schema.get('properties')
    if not isinstance(props, dict) or not props:
        return [], {}
    required = set(schema.get('required') or [])
    questions = []
    prop_specs = {}
    for prop_name, prop_spec in props.items():
        if not isinstance(prop_spec, dict):
            continue
        title = str(prop_spec.get('title') or prop_name)
        desc = str(prop_spec.get('description') or '').strip()
        label = f'{title} — {desc}' if desc else title
        enum_vals = prop_spec.get('enum')
        prop_type = str(prop_spec.get('type') or 'string')
        options = []
        if isinstance(enum_vals, list) and enum_vals:
            options = [{'id': str(v), 'label': str(v)} for v in enum_vals]
        elif prop_type == 'boolean':
            options = [{'id': 'true', 'label': '是 (true)'}, {'id': 'false', 'label': '否 (false)'}]
        questions.append({
            'id': prop_name,
            'question': label,
            'multiSelect': prop_type == 'array',
            'canSkip': prop_name not in required,
            'options': options,
        })
        prop_specs[prop_name] = prop_spec
    return questions, prop_specs


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
        return {
            'id': request_id,
            'tool': f'{prefix}MCP 信息补充 · {server_name}',
            'needsInput': True,
            'canCancel': True,
            'cancelLabel': '拒绝',
            'exclusiveWriteIn': True,
            'input': {'questions': questions},
        }, meta
    return {
        'id': request_id,
        'tool': f'{prefix}MCP 授权确认 · {server_name}',
        'needsInput': False,
        'input': _non_empty_dict(server=server_name, mode=spec.get('mode'), message=message, url=spec.get('url')),
        'decisions': [
            {'id': 'accept', 'label': '允许本次'},
            {'id': 'decline', 'label': '拒绝'},
        ],
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
    prop_type = str((prop_spec or {}).get('type') or 'string')
    if prop_type == 'array':
        return list(raw_vals)
    val = str(raw_vals[0]).strip()
    if prop_type == 'boolean':
        return val.lower() in ('true', '1', 'yes', '是 (true)', '是')
    if prop_type == 'integer':
        return int(val)
    if prop_type == 'number':
        return float(val)
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
        prop_specs = meta.get('elicitationProps')
        if prop_specs:
            if not isinstance(answers, dict):
                raise ValueError('请回答每一项问题')
            payload = {}
            for prop_name, prop_spec in prop_specs.items():
                ans_entry = answers.get(prop_name)
                if isinstance(ans_entry, dict) and ans_entry.get('skipped') is True:
                    continue
                raw_vals = ans_entry.get('answers') if isinstance(ans_entry, dict) else None
                cleaned = [str(v).strip() for v in (raw_vals or []) if isinstance(v, str) and str(v).strip()]
                if cleaned:
                    payload[prop_name] = _coerce_elicitation_value(cleaned, prop_spec)
            return {'elicitation': {'action': 'accept', 'contentJson': json.dumps(payload, ensure_ascii=False)}}
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


class AntigravityCli:
    _ALLOWED_METHODS = (
        'GetUserStatus',
        'GetAllCascadeTrajectories',
        'GetCascadeTrajectory',
        'StartCascade',
        'SendUserCascadeMessage',
        'CancelCascadeInvocation',
        'HandleCascadeUserInteraction',
    )

    def __init__(self, home=None):
        self.home = Path(home or Path.home()).resolve()
        self._lock = threading.RLock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self._trajectories_cache = {}
        self._waiting_cache = {}

    def available(self, surface='app'):
        try:
            endpoint(surface, self.home)
            return True
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def call_ls_rpc(self, method, payload=None, *, surface='app'):
        if method not in self._ALLOWED_METHODS:
            raise ValueError('不支持的 Antigravity 原生操作')
        # This serializes bridge writes; ownership remains with the native LS.
        with self._lock:
            try:
                target = endpoint(surface, self.home)
                verify_endpoint(target)
                request = urllib.request.Request(
                    target['address'] + '/exa.language_server_pb.LanguageServerService/' + method,
                    data=json.dumps(payload or {}).encode('utf-8'),
                    headers={'Content-Type': 'application/json', 'x-codeium-csrf-token': target['token']})
                with self._opener.open(request, timeout=8) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Antigravity 原生回复过大')
                result = json.loads(raw) if raw else {}
                if not isinstance(result, dict):
                    raise ValueError('Antigravity 原生回复无效')
                verify_endpoint(target)
                return result
            except (OSError, ValueError, urllib.error.URLError):
                # Do not expose native error bodies, tokens or retry a write.
                raise BridgeUnavailable('Antigravity 原生操作未确认，请检查电脑原会话后再操作') from None

    def _invalidate_trajectories_cache(self, surface=None):
        with self._lock:
            if surface is None:
                self._trajectories_cache.clear()
            else:
                self._trajectories_cache.pop(surface, None)

    def _collect_step_fallback(self, cascade_id, step_index, *, surface='app'):
        try:
            res = self.call_ls_rpc('GetCascadeTrajectory', {'cascadeId': cascade_id}, surface=surface)
        except BridgeUnavailable:
            return None
        steps = ((res.get('trajectory') or {}).get('steps')) if isinstance(res.get('trajectory'), dict) else None
        if isinstance(steps, list) and 0 <= step_index < len(steps) and isinstance(steps[step_index], dict):
            return steps[step_index]
        return None

    def waiting_state_by_cid(self, *, surface='app', max_age=2.0):
        """Return (waiting_requests_by_cid, active_cids) for a surface."""
        now = time.monotonic()
        with self._lock:
            cached = self._trajectories_cache.get(surface)
            if max_age > 0 and cached and (now - cached[0]) < max_age:
                return {k: [dict(r) for r in v] for k, v in cached[1].items()}, set(cached[2])

        res = self.call_ls_rpc('GetAllCascadeTrajectories', {}, surface=surface)
        summaries = res.get('trajectorySummaries')
        if not isinstance(summaries, dict):
            return {}, set()

        waiting_by_cid = {}
        active_cids = set()
        fresh_cache = {}
        for traj_cid, summary in summaries.items():
            if not isinstance(summary, dict):
                continue
            status = str(summary.get('status') or '')
            meta = summary.get('trajectoryMetadata') if isinstance(summary.get('trajectoryMetadata'), dict) else {}
            root_cid = str(meta.get('rootCascadeId') or meta.get('rootConversationId') or '').strip()
            parent_cid = str(meta.get('parentCascadeId') or meta.get('parentConversationId') or '').strip()
            target_cids = [traj_cid]
            for ancestor in (root_cid, parent_cid):
                if ancestor and ancestor not in target_cids:
                    target_cids.append(ancestor)
            if status in _ACTIVE_RUN_STATUSES or any(isinstance(summary.get(k), list) and summary.get(k) for k in ('waitingSteps', 'subagentWaitingSteps')):
                active_cids.add(traj_cid)

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
                fresh_cache[req_id] = item_meta
                for owner in target_cids:
                    waiting_by_cid.setdefault(owner, []).append(dict(public))

        with self._lock:
            self._trajectories_cache[surface] = (time.monotonic(), {k: [dict(r) for r in v] for k, v in waiting_by_cid.items()}, set(active_cids))
            self._waiting_cache.update(fresh_cache)
            if len(self._waiting_cache) > 256:
                for stale_key in list(self._waiting_cache.keys())[:-128]:
                    if stale_key not in fresh_cache:
                        self._waiting_cache.pop(stale_key, None)

        return waiting_by_cid, active_cids

    def has_waiting_interactions(self, conversation_id, *, surface='app'):
        if not conversation_id:
            return False
        waiting_by_cid, _ = self.waiting_state_by_cid(surface=surface)
        return bool(waiting_by_cid.get(conversation_id))

    def _resolve_waiting_meta(self, expected_cid, request_id, *, surface='app'):
        with self._lock:
            meta = self._waiting_cache.get(request_id)
        if meta is None:
            self.waiting_state_by_cid(surface=surface, max_age=0.0)
            with self._lock:
                meta = self._waiting_cache.get(request_id)
        if meta is None:
            parsed = parse_waiting_request_id(request_id)
            if parsed:
                parsed_surface, cascade_id, trajectory_id, step_index = parsed
                if parsed_surface and parsed_surface != surface:
                    raise ValueError('该互动请求来源不匹配')
                full_step = self._collect_step_fallback(cascade_id, step_index, surface=surface)
                if isinstance(full_step, dict) and full_step.get('status') == 'CORTEX_STEP_STATUS_WAITING':
                    _, meta = _normalize_waiting_step(request_id, surface, cascade_id, trajectory_id, step_index, full_step)
        if meta is None:
            raise ValueError('该互动请求已过期或已在桌面端处理')
        if meta.get('surface') != surface:
            raise ValueError('该互动请求来源不匹配')
        owners = {meta.get('cascadeId'), meta.get('rootCid'), meta.get('parentCid')} - {None, ''}
        if expected_cid and expected_cid not in owners:
            raise ValueError('该请求不属于当前会话')
        return meta

    def respond_interaction(self, conversation_id, request_id, decision, answers=None, *, surface='app'):
        """Submit user answers or approval decisions via HandleCascadeUserInteraction."""
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

    def catalog_models(self, *, refresh=False, surface='app'):
        status = self.user_status(surface)
        configs = status.get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
        models = []
        for row in configs:
            if not isinstance(row, dict) or not isinstance(row.get('modelId'), str):
                continue
            alias = row.get('modelOrAlias')
            if not isinstance(alias, dict) or not alias.get('model'):
                continue
            models.append({'id': row['modelId'], 'name': row.get('label') or row['modelId'],
                           'efforts': [], 'supportsEffort': False})
        return {'models': models, 'userStatus': status,
                'notice': '' if models else '原客户端暂未提供可用模型列表'}

    def _model(self, model, surface):
        if not model or model == 'auto':
            return None  # Preserve the original server's selected default.
        configs = self.user_status(surface).get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
        found = [row.get('modelOrAlias', {}).get('model') for row in configs
                 if isinstance(row, dict) and row.get('modelId') == model]
        if len(found) != 1 or not found[0]:
            raise ValueError('所选模型不在当前原客户端的可用目录中')
        return found[0]

    def send_user_message(self, conversation_id, prompt, model=None, effort=None, *, surface='app'):
        body = {'cascade_id': conversation_id, 'items': [{'text': prompt}]}
        selected = self._model(model, surface)
        if selected:
            body['cascade_config'] = {'planner_config': {'requested_model': {'model': selected}}}
        result = self.call_ls_rpc('SendUserCascadeMessage', body, surface=surface)
        self._invalidate_trajectories_cache(surface)
        return result

    def start_cascade(self, *, surface='app', model=None, effort=None, cwd=None):
        path = Path(cwd or '').resolve()
        if not cwd or not path.is_dir():
            raise ValueError('请选择有效的本机项目目录')
        uri = path.as_uri()
        body = {'source': 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT', 'workspace_uris': [uri]}
        selected = self._model(model, surface)
        if selected:
            body['requested_model'] = selected
        result = self.call_ls_rpc('StartCascade', body, surface=surface)
        cid = result.get('cascadeId') or result.get('cascade_id')
        if not isinstance(cid, str) or not cid:
            raise BridgeUnavailable('Antigravity 原客户端未确认新会话标识')
        environment = result.get('projectEnvInfo') or result.get('project_env_info') or {}
        if environment.get('workspaceUris', environment.get('workspace_uris')) != [uri]:
            raise BridgeUnavailable('原客户端尚未确认新会话项目绑定，首条消息未发送；请在电脑检查已创建的会话')
        self._invalidate_trajectories_cache(surface)
        return cid

    def stop(self, conversation_id, *, surface='app'):
        result = self.call_ls_rpc('CancelCascadeInvocation', {'cascade_id': conversation_id}, surface=surface)
        self._invalidate_trajectories_cache(surface)
        return result

    def close(self):
        pass
