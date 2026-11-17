"""Small process coordination helpers for Ubuntu/WSL entry points."""
import json
import os
from pathlib import Path


def acquire_lock(path):
    import fcntl
    stream = Path(path).open('a')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        raise ValueError('Процесс для этой базы уже запущен: ' + str(path)) from None
    return stream


def write_ready(path):
    if path is not None:
        path = Path(path)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'pid': os.getpid(), 'ready': True}), encoding='utf-8')
        temporary.replace(path)
