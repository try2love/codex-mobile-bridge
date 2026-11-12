"""Durable episodes for native CLI waiting-state observations, without RPC authority."""
import json
from pathlib import Path
import threading


class CliWaits:
    def __init__(self, directory, save):
        self.path = Path(directory)/'cli-notification-waits.json'
        self.save = save
        self.lock = threading.Lock()
        self.rows = json.loads(self.path.read_text()) if self.path.exists() else {}

    def project(self, identifier, state):
        wait = state.get('notificationWait') or {}
        turn, flags = wait.get('turnId'), wait.get('flags', [])
        with self.lock:
            previous = self.rows.get(identifier) or {}
            known = previous.get('flags', {}) if previous.get('turnId') == turn else {}
            current, requests = {}, []
            for flag in ('waitingOnApproval', 'waitingOnUserInput'):
                old = known.get(flag, {})
                active = flag in flags
                episode = old.get('episode', 0) + int(active and not old.get('active'))
                current[flag] = {'active': active, 'episode': episode}
                if active and turn:
                    requests.append({'id': ':'.join(['native-wait', turn, flag, str(episode)])})
            row = {'turnId': turn, 'flags': current}
            if row != previous:
                self.rows[identifier] = row
                self.save(self.path, self.rows)
            return {**state, 'notificationRequests': requests}
