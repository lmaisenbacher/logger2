# -*- coding: utf-8 -*-
"""Tests for the loggers' OpenBLAS thread default (README, "BLAS
threads"): one thread unless the environment names a number, which only
takes effect when it is set before numpy loads. Runs under pytest.

The runtime checks run in fresh interpreters: this test session has
loaded numpy long ago, and OpenBLAS reads the variable only when it
loads.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
ENV = 'OPENBLAS_NUM_THREADS'

# The number of threads numpy's bundled OpenBLAS runs with, as the
# library reports it ('None' when it cannot be asked)
THREADS = '''
import ctypes, sys
from pathlib import Path
numpy = sys.modules['numpy']
threads = None
libs = Path(numpy.__file__).resolve().parent.parent / 'numpy.libs'
for path in sorted(libs.glob('*openblas*')):
    library = ctypes.CDLL(str(path))
    for symbol in ('scipy_openblas_get_num_threads64_',
                   'openblas_get_num_threads64_', 'openblas_get_num_threads'):
        get_num_threads = getattr(library, symbol, None)
        if get_num_threads is not None and threads is None:
            threads = int(get_num_threads())
print(threads)
'''


def sets_blas_threads(node):
    """Whether `node` is the module-level `os.environ.setdefault(ENV, ...)`."""
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == 'os.environ.setdefault'
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and node.value.args[0].value == ENV)


def test_the_default_precedes_every_foreign_import():
    """Anything not in the standard library could bring numpy in. A
    `setdefault`, so a number the environment names wins."""
    tree = ast.parse((REPO / 'logger.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if sets_blas_threads(node):
            return
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or '']
        else:
            continue
        for name in names:
            assert name.split('.')[0] in sys.stdlib_module_names, (
                '%s is imported before %s is set' % (name, ENV))
    raise AssertionError('logger.py never sets %s' % ENV)


def test_importing_the_logger_runs_one_thread():
    """In a fresh interpreter without the variable, as a service starts."""
    env = {k: v for k, v in os.environ.items() if k != ENV}
    result = subprocess.run(
        [sys.executable, '-c', 'import os, logger, numpy\nprint(os.environ[%r])\n%s'
         % (ENV, THREADS)],
        capture_output=True, text=True, cwd=REPO, env=env)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-2] == '1'
    assert lines[-1] in ('1', 'None')
