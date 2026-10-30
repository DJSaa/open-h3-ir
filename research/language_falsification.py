"""Prove the language regressions detect planted defects; always restore source.

Run with the checkout's Python: .venv/bin/python research/language_falsification.py
Each target must pass before mutation; RED requires a collected assertion failure,
not an import error. Fresh bytecode directories prevent stale-cache false passes.
Do not run concurrently with tests or source edits.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('description-language', 'h3ir/language.py',
     "return 'Chinese' if han and han >= words else 'English'", "return 'English'",
     'test_independent_description_and_spoken_languages'),
    ('literal-speech', 'h3ir/language.py',
     'dialogue_markup(line, speaker, brief.description_language)', "'ALTERED SPEECH'",
     'test_two_speakers_keep_distinct_ids_and_exact_lines'),
    ('output-language-guard', 'h3ir/language.py',
     "if language == 'Chinese' and (han == 0 or words > han):",
     "if False and language == 'Chinese' and (han == 0 or words > han):",
     'test_wrong_description_language_is_error'),
]


def run_guard(test):
    with tempfile.TemporaryDirectory(prefix='h3ir-falsify-') as cache:
        env = dict(os.environ, PYTHONPYCACHEPREFIX=cache)
        return subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', 'tests/test_language_policy.py::'+test],
            cwd=ROOT, env=env, text=True, capture_output=True)


def main():
    for name, rel, anchor, defect, test in CASES:
        target = ROOT / rel
        original = target.read_bytes()
        source = original.decode('utf-8')
        if source.count(anchor) != 1:
            raise RuntimeError(f'BROKEN {name}: anchor not unique')
        baseline = run_guard(test)
        if baseline.returncode != 0:
            raise RuntimeError(f'BROKEN {name}: baseline fails\n{baseline.stdout}')
        planted = source.replace(anchor, defect)
        compile(planted, str(target), 'exec')
        try:
            target.write_text(planted, encoding='utf-8')
            if target.read_text(encoding='utf-8') != planted:
                raise RuntimeError(f'BROKEN {name}: defect not written')
            result = run_guard(test)
            if result.returncode != 1 or 'AssertionError' not in result.stdout or ' failed' not in result.stdout:
                raise RuntimeError(f'GREEN/BROKEN {name}\n{result.stdout}\n{result.stderr}')
            print(f'RED {name}: guard detects planted defect', flush=True)
        finally:
            target.write_bytes(original)
        if target.read_bytes() != original:
            raise RuntimeError(f'BROKEN {name}: restore failed')
    print('All 3 guards verified; source restored.')


if __name__ == '__main__':
    main()
