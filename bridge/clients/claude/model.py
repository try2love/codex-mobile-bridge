# Adapted from 2389859005/coding-mobile (MIT), commit 7a8f003. See LICENSE.coding-mobile.
"""Translate Desktop's session/SDK messages without publishing raw configuration."""
import uuid
import json
import re

NAMESPACE = uuid.UUID('6b9ab66b-a02a-4074-a7d6-fde5a3115173')
EMPTY_RETRY = '[Your previous response had no visible output. Please continue and produce a user-visible response.]'


def gateway_id(surface, native_id):
    return str(uuid.uuid5(NAMESPACE, surface + ':' + native_id))


def rows(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ('sessions', 'messages', 'transcript'):
            if isinstance(value.get(key), list):
                return rows(value[key])
    raise ValueError('Claude 返回了不支持的数据结构，需要更新适配器')


def session(surface, raw):
    native = raw.get('sessionId')
    if not isinstance(native, str) or not native:
        raise ValueError('Claude 会话缺少 sessionId')
    lifecycle = raw.get('lifecycleState', '')
    active = raw.get('turnRunning') is True or raw.get('isRunning') is True or lifecycle in ('running', 'initializing')
    runtime_known = (type(raw.get('turnRunning')) is bool or type(raw.get('isRunning')) is bool
                     or lifecycle in ('running', 'initializing'))
    requests = []
    for item in raw.get('pendingToolPermissions') or []:
        if not isinstance(item, dict):
            continue
        rid = item.get('requestId', item.get('id'))
        if isinstance(rid, str):
            requests.append({'id': rid, 'tool': item.get('toolName', '工具'),
                             'input': item.get('input', {}),
                             'needsInput': item.get('toolName') == 'AskUserQuestion'})
    folders = [value for value in raw.get('userSelectedFolders') or [] if isinstance(value, str)]
    return {'id': gateway_id(surface, native), 'nativeId': native, 'backend': surface, 'mode': surface,
            'host': 'local', 'title': raw.get('title') or '未命名 Claude 会话',
            'cwd': raw.get('originCwd') or raw.get('cwd') or '', 'model': raw.get('model'),
            'folders': folders,
            'effort': raw.get('effort', raw.get('effortLevel')),
            'permissionMode': raw.get('permissionMode') or 'desktop',
            'updatedAt': raw.get('lastActivityAt', raw.get('createdAt', 0)),
            'archived': bool(raw.get('isArchived') or lifecycle == 'archived'),
            'status': 'active' if active else 'idle', 'runtimeKnown': runtime_known, 'error': bool(raw.get('error')),
            'requests': requests}


def turns(value):
    """Use native terminal result IDs, never infer success from an idle session.

    Code and Cowork keep user-to-cycle pairing internally. The result UUID is
    their stable terminal event identity; pairing it with the latest user
    message would misidentify queued or steered prompts.
    """
    result = {}
    for event in rows(value):
        if event.get('type') != 'result' or event.get('parent_tool_use_id') is not None:
            continue
        if any(event.get(key) is True for key in ('isSidechain', 'isSyntheticResult', 'isSynthetic', 'isMeta', 'isCompactSummary')):
            continue
        identifier = event.get('uuid')
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        subtype = event.get('subtype')
        if event.get('is_error') is True or isinstance(subtype, str) and subtype.startswith('error_'):
            status = 'failed'
        elif subtype == 'success' and event.get('is_error') is False and event.get('num_turns') != 0:
            status = 'completed'
        else:
            continue
        result[identifier] = {'turnId': identifier, 'status': status}
    return list(result.values())


def transcript(value):
    result = []
    tasks = {}
    task_creations = {}
    def task_snapshot():
        return [dict(task) for task in tasks.values()]
    for index, event in enumerate(rows(value)):
        if event.get('isSynthetic') is True or event.get('isMeta') is True:
            continue
        kind = event.get('type')
        message = event.get('message') if isinstance(event.get('message'), dict) else event
        role = message.get('role', kind)
        if role not in ('user', 'assistant'):
            continue
        content = message.get('content', [])
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            continue
        texts = []
        text_part = 0
        event_id=event.get('uuid') or message.get('id') or str(index)
        def flush():
            nonlocal text_part
            if texts:
                result.append({'id':str(event_id)+':text:'+str(text_part), 'eventId':str(event_id), 'role':role,'text':'\n'.join(texts),
                               **{key: message[key] for key in ('model', 'effort') if isinstance(message.get(key), str)}})
                text_part += 1
                texts.clear()
        for part,block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            if block.get('type') in ('text', 'input_text', 'output_text'):
                texts.append(str(block.get('text', '')))
            elif block.get('type') == 'tool_use':
                flush();name=str(block.get('name','工具'));params=block.get('input',{})
                title=name;body='';task_changed=False
                if isinstance(params,dict):
                    title=params.get('description') or params.get('subject') or name
                    body=json.dumps(params,ensure_ascii=False,indent=2)
                    if name == 'TodoWrite':
                        tasks={str(i):{'content':str(t.get('content') or t.get('subject') or ''),'status':t.get('status','pending')} for i,t in enumerate(params.get('todos',[])) if isinstance(t,dict)}
                        body='\n'.join(('✓ ' if t['status']=='completed' else '◌ ')+t['content'] for t in tasks.values()) or body
                        task_changed=True
                    elif name == 'TaskCreate':
                        key='pending:'+str(block.get('id',str(event_id)+':'+str(part)))
                        tasks[key]={'content':str(params.get('subject') or params.get('description') or ''),'status':'pending'}
                        task_creations[str(block.get('id'))]=key;task_changed=True
                    elif name == 'TaskUpdate':
                        key=str(params.get('taskId',''))
                        if key:
                            if params.get('status')=='deleted':tasks.pop(key,None)
                            else:
                                task=tasks.setdefault(key,{'content':params.get('subject') or '任务 '+key,'status':'pending'})
                                if params.get('subject'):task['content']=params['subject']
                                if params.get('status'):task['status']=params['status']
                            task_changed=True
                result.append({'id':str(event_id)+':tool:'+str(part),'role':'activity','kind':'step','title':title,'text':body or name,'status':block.get('status',''), 'tool':name, **({'tasks':task_snapshot()} if task_changed else {})})
                if isinstance(params,dict) and name in ('Write','Edit','MultiEdit'):
                    content=params.get('content')
                    if content is None and 'new_string' in params:
                        content='\n'.join('-'+s for s in str(params.get('old_string','')).splitlines())+'\n'+'\n'.join('+'+s for s in str(params['new_string']).splitlines())
                    if name == 'MultiEdit' and isinstance(params.get('edits'), list):
                        content='\n\n'.join('\n'.join('-'+s for s in str(e.get('old_string','')).splitlines())+'\n'+'\n'.join('+'+s for s in str(e.get('new_string','')).splitlines()) for e in params['edits'] if isinstance(e,dict))
                    if content is not None:result.append({'id':str(event_id)+':code:'+str(part),'role':'activity','kind':'code','title':params.get('file_path','文件变更'),'language':'diff' if name!='Write' else '', 'changeType':'write' if name=='Write' else 'edit', 'text':str(content)})
            elif block.get('type') == 'tool_result':
                flush();content=block.get('content','')
                if isinstance(content,list):content='\n'.join(str(b.get('text','')) for b in content if isinstance(b,dict))
                created=task_creations.pop(str(block.get('tool_use_id')),None)
                if created in tasks:
                    if block.get('is_error'):tasks.pop(created)
                    else:
                        match=re.search(r'Task\s+#?(\d+)\s+created',str(content),re.I)
                        try:raw=json.loads(content);native=(raw.get('task') or raw).get('id') if isinstance(raw,dict) else None
                        except (ValueError,TypeError,AttributeError):native=None
                        key=str(native or (match.group(1) if match else ''))
                        if key:tasks[key]=tasks.pop(created)
                if content:result.append({'id':str(event_id)+':result:'+str(part),'role':'activity','kind':'step','title':'执行结果','text':str(content)[:30000],**({'tasks':task_snapshot()} if created else {})})
            elif block.get('type') in ('image', 'input_image'):
                texts.append('[图片]')
            elif block.get('type') == 'thinking_summary':
                flush()
                summary=block.get('text') or block.get('summary')
                if summary:result.append({'id':str(event_id)+':thinking:'+str(part),'role':'reasoning','kind':'reasoning','title':'思考摘要','text':str(summary)})
        flush()
    return result


def transcript_notice(value):
    """Explain an unresolved engine retry without presenting it as a reply."""
    waiting = False
    for event in rows(value):
        message = event.get('message') if isinstance(event.get('message'), dict) else event
        content = message.get('content', [])
        content = [{'type':'text','text':content}] if isinstance(content,str) else content
        text = '\n'.join(b.get('text','') for b in content if isinstance(b,dict) and b.get('type') == 'text') if isinstance(content,list) else ''
        if event.get('isSynthetic') is True and text.strip() == EMPTY_RETRY:
            waiting = True
        elif text.strip() and not event.get('isSynthetic') and not event.get('isMeta'):
            waiting = False
    return 'Claude 暂未生成可见正文，正在由桌面端重试；无需重复发送。' if waiting else ''
