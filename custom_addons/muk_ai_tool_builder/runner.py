"""Standalone restricted-script worker. No Odoo imports or database credentials.

Started with Python -I -S. The parent exposes a bounded JSON RPC capability
interface, never an ORM recordset. This is defense in depth, not an OS container.
"""
import builtins
import json
import resource
import signal
import sys


MAX_MESSAGE = 1_000_000
METHODS = {'search', 'read', 'search_count', 'create', 'write', 'unlink', 'generate_text'}


def send(payload):
    message = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(message.encode()) > MAX_MESSAGE:
        raise ValueError('Worker message limit exceeded.')
    sys.stdout.write(message + '\n')
    sys.stdout.flush()


def receive():
    line = sys.stdin.buffer.readline(MAX_MESSAGE + 2)
    if not line.endswith(b'\n') or len(line) > MAX_MESSAGE + 1:
        raise ValueError('Invalid worker message.')
    return json.loads(line)


class Context:
    def __getattr__(self, method):
        if method not in METHODS:
            raise ValueError('Unavailable capability.')

        def call(*args, **kwargs):
            send({'kind': 'call', 'method': method, 'args': args, 'kwargs': kwargs})
            response = receive()
            if 'error' in response:
                raise ValueError(response['error'])
            return response['result']
        return call


def deny_external_access(event, args):
    if event == 'open' or event == 'import' or event.startswith(
        ('socket.', 'subprocess.', 'os.', 'ctypes.', 'shutil.')
    ):
        raise PermissionError('External access is forbidden in generated scripts.')


def main():
    payload = receive()
    seconds = max(1, min(int(payload['seconds']), 300))
    memory = 256 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    signal.alarm(seconds)
    code = compile(payload['source'], '<generated-script>', 'exec')
    safe = {
        name: getattr(builtins, name) for name in (
            'all', 'any', 'bool', 'dict', 'enumerate', 'float', 'int', 'len',
            'list', 'max', 'min', 'range', 'round', 'set', 'sorted', 'str',
            'sum', 'tuple', 'zip',
        )
    }

    def bounded_range(*args):
        value = range(*args)
        if len(value) > payload['max_records']:
            raise ValueError('Generated tool range exceeds its record limit.')
        return value

    namespace = {
        '__builtins__': safe, 'ctx': Context(),
        'params': payload['arguments'], 'range': bounded_range,
    }
    steps = 0

    def trace(frame, event, arg):
        nonlocal steps
        if frame.f_code.co_filename == '<generated-script>':
            steps += 1
            if steps > 20_000:
                raise ValueError('Generated tool instruction limit exceeded.')
        return trace

    sys.addaudithook(deny_external_access)
    sys.settrace(trace)
    exec(code, namespace)
    sys.settrace(None)
    if 'result' not in namespace:
        raise ValueError('Generated script did not assign result.')
    send({'kind': 'result', 'result': namespace['result']})


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        send({'kind': 'error', 'error': '%s: %s' % (type(error).__name__, str(error)[:2000])})
        sys.exit(1)
