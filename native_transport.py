"""Native JSON protocol, bound draft validation and one-send-only guard."""
from datetime import datetime, timezone
import json
import queue
import subprocess
from runtime_lock import Refused

class NativeSession:
    """Transport methods used by the explicitly configured Session subclass."""
    def _read(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def receive(self):
        try:
            line = self.lines.get(timeout=self.timeout)
            if line is None or len(line) > 20000:
                raise ValueError()
            reply = json.loads(line.lstrip('\ufeff'))
            if not isinstance(reply, dict):
                raise ValueError()
            return reply
        except (queue.Empty, ValueError):
            self.close()
            raise Refused('native_response_unknown_no_retry') from None

    def request(self, action, **fields):
        try:
            self.process.stdin.write(json.dumps(dict(action=action, **fields), ensure_ascii=False) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.close()
            raise Refused('native_request_outcome_unknown') from None
        reply = self.receive()
        if reply.get('status') != 'ok' or reply.get('action') != action or reply.get('room') != self.room:
            self.close()
            # Do not log arbitrary native output or user draft contents.
            raise Refused('native_' + action + '_refused')
        return reply

    def close(self):
        process = self.process
        if process.poll() is None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class WindowsLineAdapter:
    def __init__(self, business_date, *, allow_test_send=False):
        self.business_date=business_date
        self.can_send=allow_test_send
        self.session=None; self.message=None; self.instance=None
        self.send_invocations=0; self.before=None; self.evidence={}; self.attempt={}

    def bound_reply(self, reply, *, draft=None, empty=None):
        instance = reply.get('window_instance')
        if not instance or (self.instance is not None and self.instance != instance):
            raise Refused('adapter_window_changed')
        if draft is not None and reply.get('draft_exact') is not draft:
            raise Refused('adapter_draft_mismatch')
        if empty is not None and reply.get('input_empty') is not empty:
            raise Refused('adapter_input_state_mismatch')
        self.instance = instance

    def prepare(self, destination, text):
        self.check(destination, text)
        if not self.session:
            raise Refused('preflight_required')
        self.attempt = dict(destination=destination, message=text, send_invocations=0,
                            intent_at=datetime.now(timezone.utc).isoformat())
        self.bound_reply(self.session.request('prepare'), draft=True, empty=False)

    def verify_prepared(self, destination, text):
        self.check(destination, text)
        self.bound_reply(self.session.request('verify'), draft=True, empty=False)

    def invoke(self, destination, text):
        self.check(destination, text)
        if not self.can_send or self.send_invocations:
            raise Refused('send_not_authorized_or_already_attempted')
        self.send_invocations = 1  # Before native I/O: lost response must not retry.
        self.attempt['send_invocations'] = 1
        reply = self.session.request('invoke')
        if reply.get('send_invocations') != 1:
            raise Refused('native_send_outcome_unknown')
        self.attempt['operation_returned_at'] = datetime.now(timezone.utc).isoformat()

    def clear_prepared(self):
        if self.send_invocations:
            raise Refused('cannot_clear_after_send_attempt')
        self.bound_reply(self.session.request('clear'), draft=False, empty=True)

    def close(self):
        if self.session:
            self.session.close()
