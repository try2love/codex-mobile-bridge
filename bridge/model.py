"""Normalize desktop state without changing the native thread or provider."""
import copy
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
import ntpath



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


def request_id(item):
    """Return the bridge submission id attached to a desktop user message."""
    return item.get("clientId") or item.get("clientUserMessageId") or item.get("client_id")


def user_display_text(text):
    """Hide desktop attachment plumbing from the phone-visible request."""
    if not isinstance(text, str) or "# Files mentioned by the user:" not in text:
        return text
    marker = "\n## My request:\n"
    header = text.find("# Files mentioned by the user:")
    boundary = text.rfind(marker)
    if header < 0 or boundary <= header:
        return text
    request = text[boundary + len(marker):].strip("\r\n")
    return request or text


def normalize_item(item):
    kind = item.get("type", "unknown")
    row = {"id": item.get("id"), "kind": kind, "status": item.get("status")}
    if kind in ("userMessage", "steeringUserMessage"):
        if kind == "steeringUserMessage":
            item = {**item, "content": item.get("input", [])}
        submission_request_id = request_id(item)
        if request_id:
            row["requestId"] = str(submission_request_id)
        text = user_display_text(text_content(item.get("content", [])))
        replies = question_replies(text)
        if replies:
            text = "\n\n".join(str(r.get("question", "")) + "\n" + str(r.get("answer", "")) for r in replies)
        row.update(role="user", text=text)
    elif kind in ("agentMessage", "assistantMessage"):
        row.update(role="assistant", text=item.get("text", ""), phase=item.get("phase"))
    elif kind in ("ImageView", "imageView"):
        raw = item.get("path", "")
        path = raw
        try:
            parsed = urlsplit(raw)
            if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
                file_path = unquote(parsed.path)
                # file:///D:/... has a leading slash on Windows too.
                if re.fullmatch(r"/[A-Za-z]:[\\/].+", file_path):
                    file_path = file_path[1:]
                path = url2pathname(file_path)
        except ValueError:
            path = raw
        row.update(role="activity", title="图片预览", text="", phase="commentary")
        if path:
            row["attachments"] = [{"type": "localImage", "path": path, "name": Path(path).name}]
    elif kind == "reasoning":
        row.update(role="activity", title="思考摘要", text="\n".join(item.get("summary", [])))
    elif kind == "commandExecution":
        row.update(role="activity", title="执行命令", text=item.get("command", ""), output=item.get("aggregatedOutput", ""), exitCode=item.get("exitCode"))
    elif kind == "fileChange":
        row.update(role="activity", title="文件变更", text="\n\n".join(str(c.get("path", "")) + "\n" + str(c.get("diff", "")) for c in item.get("changes", [])))
    elif kind in ("mcpToolCall", "dynamicToolCall"):
        row.update(role="activity", title=" · ".join(str(x) for x in (item.get("server"), item.get("tool", item.get("toolName"))) if x), text=json.dumps(item.get("arguments", {}), ensure_ascii=False, indent=2), output=json.dumps(item.get("result", item.get("contentItems", item.get("error", ""))), ensure_ascii=False, indent=2))
    elif kind == "error":
        row.update(role="error", text=item.get("message", "执行失败"))
    elif kind == "planImplementation":
        row.update(role="assistant", title="计划", text=item.get("planContent", ""))
    else:
        row.update(role="activity", title=kind, text=json.dumps(item, ensure_ascii=False, indent=2))
    # Preserve non-text attachment descriptors. They are not fetched from arbitrary URLs.
    attachments = [x for x in item.get("content", []) if isinstance(x, dict) and x.get("type") not in ("text", "input_text", "output_text")] if isinstance(item.get("content"), list) else []
    if attachments:
        row["attachments"] = attachments
    return row


def permission_mode(state):
    turns = ordered_turns(state)
    latest = state.get('latestThreadSettings') or {}
    params = (turns[-1].get('params') or {}) if turns else {}
    current = state.get('currentPermissions') or {}
    values = {**current, **params, **latest}
    profile = values.get('activePermissionProfile') or {}
    profile_id = values.get('permissions') or profile.get('id')
    policy = values.get('sandboxPolicy') or {}
    if profile_id == ':danger-full-access' or (not profile_id and policy.get('type') == 'dangerFullAccess'):
        return 'full-access' if values.get('approvalPolicy') == 'never' else 'custom'
    if profile_id == ':workspace' or (not profile_id and policy.get('type') == 'workspaceWrite'):
        if values.get('approvalPolicy') == 'on-request':
            return 'auto-review' if values.get('approvalsReviewer') == 'auto_review' else 'ask'
    return 'custom'


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
                           'text': str(item.get('arguments', item.get('input', '')))}
                calls[message['id']] = message
                messages.append(message)
            elif kind in ('function_call_output', 'custom_tool_call_output') and item.get('call_id') in calls:
                output = item.get('output', '')
                calls[item['call_id']]['output'] = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            else:
                messages.append(normalize_item(item))
        if not any(x.get("role") == "user" for x in messages):
            opening = text_content(turn.get("params", {}).get("input", []))
            if opening:
                messages.insert(0, {"id": "opening-" + str(turn.get("turnId", ordinal)), "role": "user", "kind": "userMessage", "text": opening})
        params = turn.get('params') or {}
        settings = (params.get('collaborationMode') or {}).get('settings') or {}
        model = params.get('model') or settings.get('model') or turn.get('model')
        effort = params.get('effort') or params.get('reasoningEffort') or settings.get('reasoning_effort') or turn.get('reasoningEffort')
        result.append({"model": model, "effort": effort, "id": turn.get("turnId") or str(ordinal), "status": turn.get("status"),
                       "startedAt": turn.get("turnStartedAtMs"), "messages": messages,
                       "actionable": bool(turn.get("turnId")) and turn.get("turnId") != "history",
                       "error": turn.get("error"), "diff": turn.get("diff")})
    history = state.get("turnHistory", {}).get("history", {})
    settings = state.get('latestThreadSettings') or {}
    tier = settings if 'serviceTier' in settings else (turns[-1].get('params') or {}) if turns else {}
    return {"id": state.get("id", state.get("sessionId")), "title": state.get("title") or "未命名聊天",
            "cwd": state.get("cwd"), "model": state.get("latestModel"), "provider": state.get("modelProvider"), "effort": state.get("latestReasoningEffort") or (state.get("latestThreadSettings") or {}).get("effort"),
            "connected": connected, "status": state.get("threadRuntimeStatus", {}).get("type", "idle"),
            "permissionMode": permission_mode(state),
            "collaborationMode": (state.get("latestCollaborationMode") or {}).get("mode"),
            **({'serviceTier': tier['serviceTier']} if 'serviceTier' in tier else {}),
            "goal": copy.deepcopy(state.get("threadGoal") or state.get("completedThreadGoal")),
            "turns": result, "requests": pending_requests(state),
            "historyComplete": history.get("isComplete", state.get("turnsPagination", {}).get("hasLoadedOldest", True))}


def pending_requests(state):
    turns = ordered_turns(state)
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
    return requests + async_requests(state)


def computer_use_approval(params):
    """Expose only the scope choices present in the live desktop approval."""
    meta = params.get('_meta') or {}
    if not isinstance(meta, dict) or meta.get('codex_approval_kind') != 'mcp_tool_call':
        return None
    connector = re.sub(r'[^a-z0-9]+', '-', str(meta.get('connector_id', '')).lower()).strip('-')
    tool = meta.get('tool_params') or {}
    app = tool.get('app') if isinstance(tool, dict) else None
    schema = params.get('requestedSchema') or {}
    if (params.get('mode', 'form') != 'form' or not (connector == 'computer-use' or connector.startswith('computer-use-'))
            or not isinstance(app, str) or not app.strip() or schema.get('properties') or schema.get('required')):
        return None
    modes = meta.get('persist', [])
    if isinstance(modes, str):
        modes = [modes]
    if not isinstance(modes, list):
        modes = []
    return {'app': app, 'persistModes': [mode for mode in ('session', 'always') if mode in modes]}


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
        "item/plan/requestImplementation": ("turnId", "planContent"),
        "mcpServer/elicitation/request": ("serverName", "message", "mode", "requestedSchema"),
    }
    # Identity verification is intentionally desktop-only, including its challenge data.
    verification = "openai/userVerification" in json.dumps(params.get("_meta", {})) or "openai/userVerification" in params
    result["supported"] = method in fields and not verification
    if method == "mcpServer/elicitation/request" and (params.get("mode", "form") != "form" or not form_supported(params.get("requestedSchema", {}))):
        result["supported"] = False
    if result["supported"]:
        result["params"] = {k: copy.deepcopy(params[k]) for k in fields[method] if k in params}
        if method == 'mcpServer/elicitation/request':
            computer = computer_use_approval(params)
            if computer:
                result['params']['computerUse'] = computer
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
