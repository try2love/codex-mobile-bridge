"""Limited native-goal access through the desktop Codex app-server.

The connection is reused only for goal metadata. No thread is loaded or resumed
and no turn is executed here; activation is sent through the original desktop
owner. Only three thread-goal RPCs and initialization are exposed.
"""
import json
import os
import queue
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path


GOAL_STATUSES = {"active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"}
GOAL_ACTIONS = {"create", "pause", "resume", "cancel", "edit"}
_STATUS_ALIASES = {"usage_limited": "usageLimited", "budget_limited": "budgetLimited"}


class GoalError(RuntimeError):
    pass


class GoalUnavailable(GoalError):
    pass


class GoalUnsupported(GoalError):
    pass


def sqlite_read_uri(path):
    """Build a portable read-only SQLite URI for Windows and POSIX paths."""
    return path.resolve().as_uri() + '?mode=ro'


def read_native_goal(codex_home, thread_id):
    """Read the authoritative native goal row for a local thread."""
    path = Path(codex_home) / 'goals_1.sqlite'
    if not path.is_file():
        return None
    # The desktop may hold a short write transaction while a goal changes.
    # One locked read must not make mobile fall back to a stale snapshot.
    for attempt in range(5):
        connection = None
        try:
            connection = sqlite3.connect(sqlite_read_uri(path), uri=True, timeout=.05)
            connection.row_factory = sqlite3.Row
            row = connection.execute('select * from thread_goals where thread_id = ?', (str(thread_id),)).fetchone()
            if not row:
                return None
            value = dict(row)
            status_aliases = {'usage_limited': 'usageLimited', 'budget_limited': 'budgetLimited'}
            value['status'] = status_aliases.get(value.get('status'), value.get('status'))
            value.update({
                'threadId': value.get('thread_id', str(thread_id)),
                'goalId': value.get('goal_id'),
                'tokenBudget': value.get('token_budget'),
                'tokensUsed': value.get('tokens_used'),
                'timeUsedSeconds': value.get('time_used_seconds'),
                'createdAtMs': value.get('created_at_ms'),
                'updatedAtMs': value.get('updated_at_ms'),
            })
            return value
        except sqlite3.OperationalError:
            if attempt == 4:
                return None
            time.sleep(.02)
        except (OSError, sqlite3.Error):
            return None
        finally:
            if connection:
                connection.close()
    return None


def normalize_goal(value):
    """Normalize one goal from either app-server RPC or the desktop snapshot."""
    if not isinstance(value, dict):
        return None
    value = dict(value)
    status = _STATUS_ALIASES.get(value.get("status"), value.get("status"))
    objective = str(value.get("objective") or "").strip()
    if not objective or status not in GOAL_STATUSES:
        return None
    value["objective"] = objective
    value["status"] = status
    return value


def goal_identity(goal):
    goal = normalize_goal(goal) or {}
    created = goal.get('createdAt')
    if created is None and goal.get('createdAtMs') is not None:
        created = goal['createdAtMs'] // 1000
    return {'objective': goal.get('objective'), 'createdAt': created}


def goal_command_fingerprint(action, objective, status=None):
    return json.dumps({"action": action, "objective": (objective or "").strip(), "status": status},
                      ensure_ascii=False, sort_keys=True)


def plan_goal_command(action, objective, current, *, status=None):
    """Classify one intent against the authoritative state.

    Returns ``(decision, message, expected_status)``.  ``duplicate`` means the
    authoritative state already proves the command; ``execute`` means it is the
    next legal transition; ``invalid`` must never be sent to the writer.
    """
    if action not in GOAL_ACTIONS:
        return "invalid", "目标操作无效", None
    objective = (objective or "").strip()
    current = normalize_goal(current)
    current_objective = (current or {}).get("objective", "")
    current_status = (current or {}).get("status")
    same_objective = bool(current_objective) and current_objective == objective
    if action == "create":
        if current is None or current_status == "complete":
            return "execute", "", "active"
        if same_objective and current_status == "active":
            return "duplicate", "", "active"
        return "invalid", "此聊天已有未完成目标，请先处理当前目标", None
    if action == "cancel":
        if current is None:
            return "duplicate", "", "cancelled"
        if not same_objective:
            return "invalid", "目标已发生变化，请刷新后重试", None
        return "execute", "", "cancelled"
    if action == "edit":
        if current is None or current_status != "paused":
            return "invalid", "请先暂停目标，再修改目标内容", None
        return ("duplicate" if same_objective else "execute"), "", "paused"
    expected = "paused" if action == "pause" else "active"
    predecessor = "active" if action == "pause" else "paused"
    if current is None:
        return "invalid", "当前聊天没有可操作的目标", None
    if not same_objective:
        return "invalid", "目标已发生变化，请刷新后重试", None
    if current_status == expected:
        return "duplicate", "", expected
    if current_status == predecessor or (action == "pause" and current_status in ("blocked", "usageLimited", "budgetLimited")):
        return "execute", "", expected
    return "invalid", "目标当前状态不支持暂停" if action == "pause" else "目标当前状态不支持恢复", None


GOAL_ACTIVATION_TEXTS = {
    "zh-CN": {
        "create": "目标已创建。请立即开始执行当前目标，不要重复创建目标。",
        "resume": "目标已恢复。请继续执行当前目标。",
    },
    "en-US": {
        "create": "Goal created. Start working on it now. Do not create it again.",
        "resume": "Goal resumed. Continue working on it.",
    },
}
DEFAULT_UI_LOCALE = "zh-CN"
def normalize_ui_locale(value):
    """Map web UI locales and legacy clients to an activation language."""
    if value in GOAL_ACTIVATION_TEXTS:
        return value
    normalized = str(value or "").strip().lower()
    if normalized.startswith("en"):
        return "en-US"
    return DEFAULT_UI_LOCALE


def normalize_goal_result(result):
    """Normalize either ``{"goal": ...}`` RPC envelopes or bare goal objects."""
    if not isinstance(result, dict):
        return None
    return normalize_goal(result["goal"] if "goal" in result else result)


def goal_activation(thread_id, goal, action, ui_locale=DEFAULT_UI_LOCALE, transition_id=None):
    """Return a stable id and text for the turn that starts a goal."""
    if action not in GOAL_ACTIONS:
        raise ValueError("目标启动操作无效")
    goal = normalize_goal(goal) or {}
    identity = goal.get("goalId") or goal.get("goal_id")
    if not identity:
        created = goal.get("createdAtMs") or goal.get("created_at_ms") or goal.get("createdAt")
        identity = f"created:{created}" if created else f"objective:{goal.get('objective', '')}"
    command_id = str(uuid.uuid5(uuid.UUID(str(thread_id)), f"goal-activation:v1:{action}:{transition_id or identity}"))
    return command_id, GOAL_ACTIVATION_TEXTS[normalize_ui_locale(ui_locale)][action]


def goal_state_confirms_command(command, current):
    """Whether readable state proves an already-sent command completed."""
    if not isinstance(command, dict):
        return False
    current = normalize_goal(current)
    if command.get('action') == 'cancel':
        return current is None
    before = command.get('before')
    if before and command.get('action') in ('pause', 'resume') and goal_identity(before) != goal_identity(current):
        return False
    decision, _, _ = plan_goal_command(
        command.get("action"), command.get("objective"), current, status=command.get("status"))
    return decision == "duplicate"


def native_goal_absent(codex_home, thread_id):
    """Return True only when readable native storage proves the goal is absent."""
    path = Path(codex_home) / 'goals_1.sqlite'
    if not path.is_file():
        return False
    connection = None
    try:
        connection = sqlite3.connect(sqlite_read_uri(path), uri=True, timeout=.05)
        return connection.execute('select 1 from thread_goals where thread_id = ?',
                                  (str(thread_id),)).fetchone() is None
    except sqlite3.Error:
        return False
    finally:
        if connection:
            connection.close()


class GoalRPC:
    """Persistent, method-whitelisted app-server client for goal state."""
    METHODS = {
        'initialize',
        'thread/goal/get',
        'thread/goal/set',
        'thread/goal/clear',
    }

    def __init__(self, home, executable, *, executable_getter=None):
        self.home, self._executable = Path(home), Path(executable) if executable else None
        self.executable_getter = executable_getter
        self.process = None
        self.messages = None
        self.counter = 0
        self.lock = threading.RLock()

    @property
    def executable(self):
        value = self.executable_getter() if self.executable_getter else self._executable
        return Path(value) if value else None

    def start(self):
        with self.lock:
            if self.process:
                return self
            executable = self.executable
            if not executable or not executable.is_file():
                raise GoalUnavailable('找不到桌面 App 的 Codex 运行时')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            try:
                messages = queue.Queue()
                self.messages = messages
                self.process = subprocess.Popen(
                    [str(executable), 'app-server', '--listen', 'stdio://'], cwd=self.home,
                    env={**os.environ, 'CODEX_HOME': str(self.home)}, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', **options)
                reader = threading.Thread(target=self._read, args=(messages, self.process.stdout), daemon=True)
                reader.start()
                request_id = self._send('initialize', {'clientInfo': {'name': 'codex_mobile_goal', 'version': '0.1'},
                                          'capabilities': {'experimentalApi': True}})
                self._receive(request_id)
                self.process.stdin.write('{"method":"initialized"}\n')
                self.process.stdin.flush()
                return self
            except Exception as exc:
                self.close()
                raise GoalUnavailable('无法连接 Codex 原生目标服务，请检查电脑端 Codex') from exc

    def _read(self, messages, stdout):
        try:
            for line in stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            messages.put(None)

    def _send(self, method, params):
        self.counter += 1
        request_id = self.counter
        try:
            self.process.stdin.write(json.dumps({'id': request_id, 'method': method, 'params': params}) + '\n')
            self.process.stdin.flush()
        except (OSError, AttributeError) as exc:
            raise GoalUnavailable('Codex 原生目标服务已断开') from exc
        return request_id

    def request(self, method, params=None):
        if method not in self.METHODS:
            raise ValueError('目标接口只允许读取、设置或取消目标')
        with self.lock:
            self.start()
            request_id = self._send(method, params)
            return self._receive(request_id)

    def _receive(self, request_id):
        deadline = time.monotonic() + 15
        while True:
            try:
                value = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty as exc:
                self.close()
                raise GoalError('Codex 原生目标操作超时') from exc
            if value is None:
                self.close()
                raise GoalUnavailable('Codex 原生目标服务已断开')
            if value.get('id') != request_id:
                if time.monotonic() >= deadline:
                    self.close()
                    raise GoalError('Codex 原生目标操作超时')
                continue
            if 'error' in value:
                error = value['error'] or {}
                message = str(error.get('message') or 'Codex 原生目标操作未完成')
                if error.get('code') in (-32601, 'method_not_found') or 'method not found' in message.lower():
                    raise GoalUnsupported('桌面 Codex 版本不支持原生目标模式')
                raise GoalError('Codex 原生目标操作未完成，请检查电脑端 Codex')
            return value.get('result') or {}

    def get_goal(self, thread_id):
        return normalize_goal_result(self.request('thread/goal/get', {'threadId': str(thread_id)}))

    def set_goal(self, thread_id, objective):
        params = {'threadId': str(thread_id), 'objective': objective, 'status': 'active'}
        return normalize_goal_result(self.request('thread/goal/set', params))

    def set_goal_status(self, thread_id, status):
        if status not in ('active', 'paused'):
            raise ValueError('目标状态无效')
        params = {'threadId': str(thread_id), 'status': status}
        return normalize_goal_result(self.request('thread/goal/set', params))

    def edit_goal(self, thread_id, objective, token_budget=None):
        params = {'threadId': str(thread_id), 'objective': objective, 'status': 'paused'}
        if token_budget is not None:
            params['tokenBudget'] = token_budget
        return normalize_goal_result(self.request('thread/goal/set', params))

    def clear_goal(self, thread_id):
        return self.request('thread/goal/clear', {'threadId': str(thread_id)})

    def close(self):
        with self.lock:
            process, self.process = self.process, None
        if not process:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        try:
            process.stdout.close()
        except OSError:
            pass
