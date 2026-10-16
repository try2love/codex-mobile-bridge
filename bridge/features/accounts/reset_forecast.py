"""Shared public reset outlook; account credentials never leave the account service."""
import copy
import json
import math
import threading
import time
import uuid
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from bridge.features.auth.tls import client_context
from bridge.features.notifications.channels import NoRedirect


SOURCE = 'https://codex-reset.com/'
URL = SOURCE + 'api/forecast'
USER_AGENT = 'codex-mobile-bridge (+https://github.com/heiqiu47/codex-mobile-bridge)'
INTERVAL = 1800
MAX_AGE = 5400
OPEN_COOLDOWN = 60


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.timestamp() if date.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def probability(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100 else None


def post_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        parts = url.path.strip('/').split('/')
        if (url.scheme == 'https' and url.netloc in ('x.com', 'twitter.com') and
                len(parts) == 3 and parts[0].lower() == 'thsottiaux' and parts[1] == 'status' and parts[2].isdigit()):
            return value
    except ValueError:
        pass
    return None


def normalize(value, now):
    if not isinstance(value, dict) or not timestamp(value.get('updated_at')):
        raise ValueError('Invalid forecast')
    updated = timestamp(value['updated_at'])
    if updated > now + 60:
        raise ValueError('Future forecast')
    odds = value.get('probabilities')
    odds = odds if isinstance(odds, dict) else {}
    reset = timestamp(value.get('last_reset_at'))
    if reset and reset > now + 60:
        reset = None
    signal = value.get('official_signal')
    official = None
    if isinstance(signal, dict) and signal:
        at = timestamp(signal.get('at'))
        window = signal.get('window')
        ends = timestamp(window.get('end_at')) if isinstance(window, dict) else None
        # Optional signal fields may disappear; the documented presence still renders a generic preannouncement.
        observed = at or updated
        if observed <= now and (reset is None or observed > reset) and (ends is None or ends > now):
            official = {'at': at, 'endsAt': ends}
            summary = signal.get('summary')
            if isinstance(summary, str) and summary.strip():
                official['summary'] = summary.strip()[:20000]
            url = post_url(signal.get('url'))
            if url:
                official['url'] = url
    return {'state': 'ready', 'source': SOURCE, 'updatedAt': updated, 'checkedAt': now,
            'lastResetAt': reset, 'officialSignal': official,
            'probabilities': {'h24': probability(odds.get('rounded_24h')),
                              'h48': probability(odds.get('rounded_48h'))},
            'confidence': value.get('confidence') if value.get('confidence') in ('low', 'medium', 'high') else None}


def retry_after(value, now):
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return max(INTERVAL, seconds)
    except (TypeError, ValueError):
        pass
    try:
        return max(INTERVAL, parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        return INTERVAL


class ResetForecast:
    def __init__(self):
        self.instance_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.worker = None
        self.next_check = 0
        self.last_request = 0
        self.retry_until = 0
        self.request_id = 0
        self.finished_at = 0
        self.changed = False
        self.value = {'state': 'idle', 'source': SOURCE}

    def status(self):
        with self.lock:
            value = copy.deepcopy(self.value)
            value['refresh'] = {'instanceId': self.instance_id, 'id': self.request_id, 'checking': bool(self.worker and self.worker.is_alive()),
                                'finishedAt': self.finished_at, 'changed': self.changed,
                                'cooldownUntil': self.last_request + OPEN_COOLDOWN if self.last_request else 0,
                                'retryAt': self.retry_until}
        now = time.time()
        signal = value.get('officialSignal')
        if signal and signal.get('endsAt') is not None and signal['endsAt'] <= now:
            value['officialSignal'] = None
        if value.get('updatedAt'):
            value['stale'] = (value['state'] == 'error' or
                              now - value.get('publishedAt', value['updatedAt']) > MAX_AGE)
        return value

    def request(self, refresh=False):
        with self.lock:
            now = time.time()
            if (self.closed.is_set() or now < self.retry_until or now-self.last_request < OPEN_COOLDOWN or
                    not refresh and now < self.next_check or self.worker and self.worker.is_alive()):
                return
            self.last_request = now
            self.request_id += 1
            self.next_check = now + INTERVAL
            if not self.value.get('updatedAt'):
                self.value['state'] = 'loading'
            self.worker = threading.Thread(target=self._read, name='reset-forecast', daemon=True)
            self.worker.start()

    def _read(self):
        try:
            opener = build_opener(NoRedirect, HTTPSHandler(context=client_context()))
            request = Request(URL, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
            with opener.open(request, timeout=10) as response:
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise ValueError('Forecast too large')
                now = time.time()
                value = normalize(json.loads(raw), now)
                published = timestamp(response.headers.get('x-published-checked-at'))
                if published is not None and published <= now + 60:
                    value['publishedAt'] = published
            signal = value.get('officialSignal')
            if signal and signal.get('url'):
                self._read_announcement(opener, signal)
            with self.lock:
                if not self.closed.is_set():
                    fields = ('lastResetAt', 'officialSignal', 'probabilities', 'confidence')
                    self.changed = any(self.value.get(key) != value.get(key) for key in fields)
                    value['checkedAt'] = time.time()
                    self.value = value
        except Exception as error:
            limited = isinstance(error, HTTPError) and error.code == 429
            now = time.time()
            delay = retry_after(error.headers.get('Retry-After'), now) if limited else OPEN_COOLDOWN
            with self.lock:
                self.next_check = max(self.next_check, now + delay) if limited else now + delay
                if limited:
                    self.retry_until = self.next_check
                self.value.update(state='error')
        finally:
            with self.lock:
                self.finished_at = time.time()

    def _read_announcement(self, opener, signal):
        try:
            request = Request(SOURCE + 'api/feed?locale=zh', headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
            with opener.open(request, timeout=10) as response:
                raw = response.read(262145)
                if len(raw) > 262144:
                    return
                feed = json.loads(raw)
            fetched = timestamp(feed.get('fetched_at')) if isinstance(feed, dict) else None
            now = time.time()
            if not fetched or not now - MAX_AGE <= fetched <= now + 60 or feed.get('stale') is not False:
                return
            tweets = feed.get('tweets')
            if not isinstance(tweets, list):
                return
            identity = urlsplit(signal['url']).path.rstrip('/').split('/')[-1]
            for tweet in tweets:
                if not isinstance(tweet, dict) or str(tweet.get('id')) != identity:
                    continue
                text = tweet.get('text')
                if isinstance(text, str) and text.strip():
                    signal['text'] = text
                    translated = tweet.get('localized_text')
                    if tweet.get('translation_status') == 'translated' and isinstance(translated, str) and translated.strip():
                        signal['translation'] = {'locale': 'zh', 'text': translated}
                return
        except Exception as error:
            # An optional post read must preserve the valid forecast and its labelled source summary.
            if isinstance(error, HTTPError) and error.code == 429:
                with self.lock:
                    self.retry_until = max(self.retry_until, time.time() + retry_after(error.headers.get('Retry-After'), time.time()))
                    self.next_check = max(self.next_check, self.retry_until)

    def close(self):
        self.closed.set()
        if self.worker:
            self.worker.join(timeout=2)
