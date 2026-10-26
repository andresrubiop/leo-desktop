#!/usr/bin/env python3
"""Opt-in real local Ollama + real read-only account/skill inventory.

Run explicitly: python3 tests/integration_harness.py
Uses configured local model, real metadata/cache; may refresh Codex official RPC.
Does not unlock API secrets or execute skills. No assertion here proves Claude live API.
"""
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from leo.harness import Harness
from leo.providers import Ollama
from leo.config import Settings
from leo.usage.hub import Hub


def main():
    h = Harness(Hub())
    model = Settings().snapshot()['model']
    history = []
    for question in ('Leo, ¿me quedan tokens? Dime el uso de Claude y Codex por cuenta.',
                     '¿Y cuándo se renueva la cuota semanal de Claude?'):
        context, trace = h.context(question, history)
        assert trace and 'usage.accounts' in trace
        report = json.loads(context[context.index('{'):])['usage']
        answer = Ollama().chat(question, model=model, context=context, history=history)
        print(question, '\n', answer, '\n', flush=True)
        assert not re.search(r'no (?:tengo|puedo tener) acceso', answer, re.I), 'Model denied attached data'
        for account in report['accounts']:
            if not account['windows']:
                continue
            assert account['profile'].lower() in answer.lower(), 'Missing profile: ' + account['profile']
            for window in account['windows']:
                if 'semanal' in question and '7d' not in window['window']:
                    continue
                remaining = window['remaining_percent']
                if remaining is not None:
                    assert re.search(r'(?<!\d)' + re.escape(f'{remaining:g}') + r'(?:[.,]0)?(?:\s*%|\s*\|)', answer), 'Missing remaining quota: ' + str(remaining)
        history += [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]
    print('PASS: real account readings + local LLM, profiles/remaining quota and follow-up; no remote inference')


if __name__ == '__main__':
    main()
