"""Omit indented frames only inside a complete standard Python traceback."""
import re

PREFIX = re.compile(r'^(?:[^|\r\n]+\| )?\s*\d{4}-\d{2}-\d{2}T\S+ ')
TERMINAL = re.compile(r'^[A-Za-z_]\w*(?:\.\w+)*:')


def select_traceback(text):
    lines = text.splitlines(keepends=True)
    messages = [PREFIX.sub('', line).rstrip('\r\n') for line in lines]
    omitted = set()
    start = None
    for i, message in enumerate(messages):
        if message == 'Traceback (most recent call last):':
            start = i
        elif start is not None:
            if TERMINAL.match(message):
                omitted.update(j for j in range(start + 1, i)
                               if messages[j].startswith(('  ', '\t')))
                start = None
            elif message and not message[0].isspace():
                # An unrelated unindented event interrupts the stack.
                start = None
    kept = [i + 1 for i in range(len(lines)) if i not in omitted]
    return ''.join(lines[i - 1] for i in kept), kept, sorted(i + 1 for i in omitted)
