"""Parent side of the isolated script runner; ORM stays in the trusted process."""
import json
import os
import select
import subprocess
import sys
import time

from odoo import _
from odoo.exceptions import UserError, ValidationError


def run_isolated(source, arguments, context, seconds, max_records):
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'runner.py')
    deadline = time.monotonic() + seconds
    process = subprocess.Popen(
        [sys.executable, '-I', '-S', path], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        close_fds=True, cwd='/tmp', env={'LANG': 'C.UTF-8'},
    )
    buffer = b''

    def send(value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() + b'\n'
        if len(data) > 1_000_001:
            raise UserError(_('Generated tool RPC message is too large.'))
        # Nonblocking writes also respect the deadline if a child stops reading.
        view = memoryview(data)
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [process.stdin], [], remaining)[1]:
                raise UserError(_('Generated tool runtime limit exceeded.'))
            try:
                view = view[os.write(process.stdin.fileno(), view[:4096]):]
            except BlockingIOError:
                continue

    try:
        os.set_blocking(process.stdin.fileno(), False)
        send({'source': source, 'arguments': arguments, 'seconds': seconds, 'max_records': max_records})
        calls = 0
        while True:
            if b'\n' not in buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                    raise UserError(_('Generated tool runtime limit exceeded.'))
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise UserError(_('Script worker stopped (runtime, memory or execution failure).'))
                buffer += chunk
                if len(buffer) > 1_000_001:
                    raise UserError(_('Generated tool RPC message is too large.'))
                continue
            line, buffer = buffer.split(b'\n', 1)
            message = json.loads(line)
            if message.get('kind') == 'result':
                return message.get('result')
            if message.get('kind') == 'error':
                raise ValidationError(message.get('error') or _('Script worker failed.'))
            method = message.get('method')
            if message.get('kind') != 'call' or method not in (
                'search', 'read', 'search_count', 'create', 'write', 'unlink', 'generate_text'
            ):
                raise ValidationError(_('Invalid worker capability request.'))
            calls += 1
            if calls > 2000 or time.monotonic() >= deadline:
                raise UserError(_('Generated tool call/runtime limit exceeded.'))
            # Only an explicitly whitelisted method on the trusted facade can run.
            result = getattr(context, method)(*message.get('args', []), **message.get('kwargs', {}))
            if time.monotonic() >= deadline:
                raise UserError(_('Generated tool runtime limit exceeded.'))
            send({'result': result})
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
