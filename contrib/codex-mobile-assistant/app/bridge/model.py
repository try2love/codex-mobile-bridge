"""Normalize desktop state without changing the native thread or provider."""
import copy
import hashlib
import json
import re
from .attachments import split_attachment_prompt
from .summary_language import strip_summary_language


PERMISSION_PRESETS = {
    'auto': {'permissions': ':workspace', 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'},
    'guardian-approvals': {'permissions': ':workspace', 'approvalPolicy': 'on-request', 'approvalsReviewer': 'guardian_subagent'},
    'full-access': {'permissions': ':danger-full-access', 'approvalPolicy': 'never', 'approvalsReviewer': 'user'},
}


PERMISSION_KEYS = ('permissions', 'activePermissionProfile', 'sandboxPolicy', 'approvalPolicy')


def permission_settings_mode(settings):
    if not isinstance(settings, dict):
        return 'unknown'
    if not any(settings.get(key) is not None for key in PERMISSION_KEYS):
        return 'unknown'
    active_profile = settings.get('activePermissionProfile') or {}
    profile = settings.get('permissions') or active_profile.get('id')
    if active_profile.get('extends') is not None and not settings.get('permissions'):
        return 'custom'
    sandbox = settings.get('sandboxPolicy') or {}
    sandbox_profile = {'readOnly': ':read-only', 'read-only': ':read-only',
                       'workspaceWrite': ':workspace', 'workspace-write': ':workspace',
                       'dangerFullAccess': ':danger-full-access', 'danger-full-access': ':danger-full-access',
                       'disabled': ':danger-full-access'}.get(sandbox.get('type'))
    if profile and sandbox_profile and profile != sandbox_profile:
        return 'custom'
    profile = profile or sandbox_profile
    if profile == ':read-only':
        return 'read-only'
    if not (settings.get('permissions') or active_profile.get('id')) and sandbox.get('type') in ('workspaceWrite', 'workspace-write') and any(
            sandbox.get(flag) is not False for flag in ('networkAccess', 'excludeSlashTmp', 'excludeTmpdirEnvVar')):
        return 'custom'
    reviewer = settings.get('approvalsReviewer') or 'user'
    if reviewer == 'auto_review':
        reviewer = 'guardian_subagent'
    for mode, preset in PERMISSION_PRESETS.items():
        if (profile == preset['permissions'] and settings.get('approvalPolicy') == preset['approvalPolicy']
                and (mode == 'full-access' or reviewer == preset['approvalsReviewer'])):
            return mode
    return 'custom'


def permission_mode(state):
    turns = ordered_turns(state)
    sources = [state.get('latestThreadSettings'),
               turns[-1].get('params') if turns else None, state.get('currentPermissions')]
    return next((permission_settings_mode(source) for source in sources
                 if isinstance(source, dict) and any(source.get(key) is not None for key in PERMISSION_KEYS)), 'unknown')


def effective_permission_mode(state):
    turns = ordered_turns(state)
    # Live permissions describe the actual sandbox; saved settings can target a later turn.
    sources = [state.get('currentPermissions'), turns[-1].get('params') if turns else None]
    return next((permission_settings_mode(source) for source in sources
                 if isinstance(source, dict) and any(source.get(key) is not None for key in PERMISSION_KEYS)), 'unknown')


def apply_patches(state, patches):
    for patch in patches:
        path = patch.get("path", [])
        if isinstance(path, str):
            path = [p.replace("~1", "/").replace("~0", "~") for p in path.split("/")[1:]]
        if not path:
            if patch["op"] != "replace":
                raise ValueError("Unsupported root patch")
            state = copy.deepcopy(patch["value"])
            continue
        target = state
        for key in path[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        key = path[-1]
        operation = patch["op"]
        if isinstance(target, list):
            index = len(target) if key == "-" else int(key)
            if operation == "add":
                target.insert(index, copy.deepcopy(patch["value"]))
            elif operation == "remove":
                target.pop(index)
            elif operation == "replace":
                target[index] = copy.deepcopy(patch["value"])
            else:
                raise ValueError("Unsupported list patch")
        elif operation == "remove":
            del target[key]
        elif operation in ("replace", "add"):
            target[key] = copy.deepcopy(patch["value"])
        else:
            raise ValueError("Unsupported patch")
    return state


def ordered_turns(state):
    history = state.get("turnHistory", {})
    if history.get("kind") != "canonical":
        return state.get("turns", [])
    history = history.get("history", {})
    entities = history.get("entitiesByKey", {})
    turns, seen = [], set()
    for island in history.get("islands", []):
        for entry in island.get("entries", []):
            key = entry.get("value")
            if key in entities and key not in seen:
                seen.add(key)
                turns.append(entities[key])
    return turns


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict) and x.get("type") in ("text", "input_text", "output_text", "inputText"))
    return ""


def items_array(items):
    if isinstance(items, list):
        return items
    if isinstance(items, dict):
        entities = items.get("entitiesByKey", {})
        if entities:
            return [entities[e["value"]] for island in items.get("islands", []) for e in island.get("entries", []) if e.get("value") in entities]
        return list(items.get("items", []))
    return []


def summary_text(value):
    """Read only displayable summary text, never encrypted reasoning or arbitrary metadata."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n\n'.join(filter(None, (summary_text(part) for part in value)))
    if isinstance(value, dict) and value.get('type') in (None, 'summary_text', 'summaryText', 'text'):
        return summary_text(value.get('text'))
    return ''


def short_activity_text(value, limit=180):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def activity_filename(value):
    return str(value or '').replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def command_summary(item):
    actions = item.get('commandActions') or []
    descriptions = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        kind = action.get('type')
        target = activity_filename(action.get('name') or action.get('path'))
        if kind == 'read':
            descriptions.append('读取文件' + ('：' + target if target else ''))
        elif kind == 'search':
            descriptions.append('搜索内容' + ('：' + short_activity_text(action.get('query'), 60) if action.get('query') else ''))
        elif kind == 'listFiles':
            descriptions.append('查看目录' + ('：' + target if target else ''))
    if descriptions:
        return '；'.join(dict.fromkeys(descriptions))[:220]
    command = str(item.get('command') or '')
    for name, label in (('powershell', 'PowerShell'), ('pwsh', 'PowerShell'), ('python', 'Python'), ('node', 'Node.js')):
        if re.search(r'(?i)\b' + name + r'(?:\.exe)?\b', command[:400]):
            return '运行 ' + label + ' 脚本'
    return '执行终端命令；完整命令和输出可在下方查看。'


def tool_summary(name, arguments):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    if isinstance(arguments, dict):
        title = short_activity_text(arguments.get('title') or arguments.get('description'))
        if title:
            return title
        if isinstance(arguments.get('cmd'), str):
            return command_summary({'command': arguments['cmd']})
    return '调用工具：' + str(name or '工具')


def normalize_item(item):
    kind = item.get("type", "unknown")
    row = {"id": item.get("id"), "kind": kind, "status": item.get("status")}
    uploaded_files = []
    if kind in ("userMessage", "steeringUserMessage"):
        if kind == "steeringUserMessage":
            item = {**item, "content": item.get("input", [])}
        text = text_content(item.get("content", []))
        row["timestampKey"] = hashlib.sha256(text.encode()).hexdigest()
        text = strip_summary_language(text)
        text, uploaded_files = split_attachment_prompt(text)
        replies = question_replies(text)
        if replies:
            text = "\n\n".join(str(r.get("question", "")) + "\n" + str(r.get("answer", "")) for r in replies)
        row.update(role="user", text=text)
    elif kind in ("agentMessage", "assistantMessage"):
        row.update(role="assistant", text=item.get("text", ""), phase=item.get("phase"))
        row["timestampKey"] = hashlib.sha256((row["text"] or "").encode()).hexdigest()
    elif kind == "reasoning":
        row.update(role="activity", title="思考摘要", text=summary_text(item.get("summary")))
    elif kind == "commandExecution":
        row.update(role="activity", title="执行命令", text=item.get("command", ""), output=item.get("aggregatedOutput", ""), exitCode=item.get("exitCode"),
                   activitySummary=command_summary(item), cwd=item.get("cwd"), durationMs=item.get("durationMs"))
    elif kind == "fileChange":
        row.update(role="activity", title="文件变更", text="\n\n".join(str(c.get("path", "")) + "\n" + str(c.get("diff", "")) for c in item.get("changes", [])),
                   activitySummary="修改文件：" + "、".join(activity_filename(c.get("path")) for c in item.get("changes", [])[:3]))
    elif kind in ("mcpToolCall", "dynamicToolCall"):
        row.update(role="activity", title=" · ".join(str(x) for x in (item.get("server"), item.get("tool", item.get("toolName"))) if x), text=json.dumps(item.get("arguments", {}), ensure_ascii=False, indent=2), output=json.dumps(item.get("result", item.get("contentItems", item.get("error", ""))), ensure_ascii=False, indent=2),
                   activitySummary=tool_summary(item.get("tool", item.get("toolName")), item.get("arguments")))
    elif kind == "error":
        row.update(role="error", text=item.get("message", "执行失败"))
    else:
        row.update(role="activity", title=kind, text=json.dumps(item, ensure_ascii=False, indent=2))
    # Preserve non-text attachment descriptors. They are not fetched from arbitrary URLs.
    attachments = uploaded_files + ([x for x in item.get("content", []) if isinstance(x, dict) and x.get("type") not in ("text", "input_text", "output_text")] if isinstance(item.get("content"), list) else [])
    if attachments:
        row["attachments"] = attachments
    for field in ("sentAt", "completedAt"):
        if item.get(field) is not None:
            row[field] = item[field]
    return row


def normalize_state(state, connected=True):
    turns = ordered_turns(state)
    result = []
    for ordinal, turn in enumerate(turns):
        items = items_array(turn.get("items", []))
        messages, calls = [], {}
        for item in items:
            kind = item.get('type')
            if kind in ('function_call', 'custom_tool_call'):
                message = {'id': item.get('call_id', item.get('id')), 'kind': 'storedToolEvent',
                           'role': 'activity', 'title': item.get('name') or '工具调用',
                           'text': str(item.get('arguments', item.get('input', ''))),
                           'activitySummary': tool_summary(item.get('name'), item.get('arguments', item.get('input', '')))}
                calls[message['id']] = message
                messages.append(message)
            elif kind in ('function_call_output', 'custom_tool_call_output') and item.get('call_id') in calls:
                output = item.get('output', '')
                calls[item['call_id']]['output'] = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            else:
                message = normalize_item(item)
                if kind == 'commandExecution':
                    pending = next((request.get('params', {}) for request in state.get('requests', [])
                                    if item.get('id') and request.get('params', {}).get('itemId') == item.get('id')), {})
                    reason = short_activity_text(pending.get('reason'))
                    if reason:
                        message['activitySummary'] = reason
                messages.append(message)
        if not any(x.get("role") == "user" for x in messages):
            opening = text_content(turn.get("params", {}).get("input", []))
            if opening:
                messages.insert(0, {"id": "opening-" + str(turn.get("turnId", ordinal)), "role": "user", "kind": "userMessage", "text": opening,
                                    "timestampKey": hashlib.sha256(opening.encode()).hexdigest()})
        result.append({"id": turn.get("turnId") or str(ordinal), "status": turn.get("status"),
                       "startedAt": turn.get("turnStartedAtMs") or turn.get("startedAt"),
                       "completedAt": turn.get("turnCompletedAtMs") or turn.get("completedAt"), "messages": messages,
                       "error": turn.get("error"), "diff": turn.get("diff")})
    history = state.get("turnHistory", {}).get("history", {})
    requests = []
    for request in state.get("requests", []):
        normalized = normalize_request(request)
        item_id = request.get("params", {}).get("itemId")
        related = next((item for turn in turns for item in items_array(turn.get("items")) if item.get("id") == item_id), None) if item_id else None
        if related and normalized["supported"]:
            if request.get("method") == "item/fileChange/requestApproval":
                normalized["params"]["changes"] = copy.deepcopy(related.get("changes", []))
            elif request.get("method") == "item/commandExecution/requestApproval" and not normalized["params"].get("command"):
                normalized["params"]["command"] = related.get("command", "")
        requests.append(normalized)
    return {"id": state.get("id", state.get("sessionId")), "title": state.get("title") or "未命名聊天",
            "cwd": state.get("cwd"), "model": state.get("latestModel"), "provider": state.get("modelProvider"), "effort": state.get("latestReasoningEffort") or (state.get("latestThreadSettings") or {}).get("effort"),
            "connected": connected, "status": state.get("threadRuntimeStatus", {}).get("type", "idle"),
            "permissionMode": permission_mode(state), "effectivePermissionMode": effective_permission_mode(state),
            "latestError": turns[-1].get('error') if turns else None,
            "turns": result, "requests": requests + async_requests(state),
            "historyComplete": history.get("isComplete", state.get("turnsPagination", {}).get("hasLoadedOldest", True))}


def normalize_request(request):
    result = {"id": request.get("id"), "method": request.get("method"), "params": {}}
    params = request.get("params", {})
    method = request.get("method")
    fields = {
        "item/commandExecution/requestApproval": ("command", "cwd", "reason", "availableDecisions", "networkApprovalContext"),
        "item/fileChange/requestApproval": ("reason", "grantRoot", "changes"),
        "item/permissions/requestApproval": ("reason", "permissions"),
        "item/tool/requestUserInput": ("questions",),
        "tool/requestUserInput": ("questions",),
        "mcpServer/elicitation/request": ("serverName", "message", "mode", "requestedSchema"),
    }
    # Identity verification is intentionally desktop-only, including its challenge data.
    verification = "openai/userVerification" in json.dumps(params.get("_meta", {})) or "openai/userVerification" in params
    result["supported"] = method in fields and not verification
    if method == "mcpServer/elicitation/request" and (params.get("mode", "form") != "form" or not form_supported(params.get("requestedSchema", {}))):
        result["supported"] = False
    if result["supported"]:
        result["params"] = {k: copy.deepcopy(params[k]) for k in fields[method] if k in params}
    else:
        result["params"] = {"message": "请在桌面 App 处理此请求"}
    return result


def form_supported(schema):
    if schema.get("type") != "object" or set(schema) - {"type", "properties", "required", "title", "description", "$schema", "additionalProperties"}:
        return False
    allowed = {"type", "title", "description", "enum", "enumNames", "default", "minLength", "maxLength", "minimum", "maximum"}
    return all(isinstance(field, dict) and not set(field) - allowed and field.get("type", "string") in ("string", "number", "integer", "boolean") for field in schema.get("properties", {}).values())


def question_replies(text):
    prefix, suffix = "<send_user_message_question_reply>", "</send_user_message_question_reply>"
    if not text.strip().startswith(prefix) or not text.strip().endswith(suffix):
        return []
    try:
        value = json.loads(text.strip()[len(prefix):-len(suffix)])
        return value if isinstance(value, list) and all(isinstance(r, dict) for r in value) else []
    except ValueError:
        return []


def async_requests(state):
    result = []
    turns = ordered_turns(state)
    for turn in turns[-1:]:
        if turn.get("status") not in ("inProgress", "completed"):
            continue
        items = items_array(turn.get("items"))
        answered = set()
        for item in items:
            if item.get("type") == "userMessage" or (item.get("type") == "steeringUserMessage" and item.get("status") == "accepted"):
                text = text_content(item.get("content", item.get("input", [])))
                answered.update(r.get("questionItemId") for r in question_replies(text))
        for item in items:
            if item.get("type") != "agentMessage" or not item.get("questions"):
                continue
            questions = []
            for index, question in enumerate(item["questions"]):
                key = json.dumps(["request_user_input_async", item["id"], index], ensure_ascii=False, separators=(",", ":"))
                if key not in answered:
                    questions.append({"id": key, "question": question["title"], "options": [{"label": o} for o in (question.get("options") or [])]})
            if questions:
                result.append({"id": "async:" + str(turn.get("turnId")) + ":" + item["id"], "method": "bridge/requestUserInputAsync",
                               "supported": True, "params": {"questions": questions}})
    return result
