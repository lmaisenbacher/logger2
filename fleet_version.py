"""Software version and build provenance of a fleet process.

Version policy - manual SemVer, one number per process:

- MAJOR: a change older readers of the process's database series would
  misread (renamed or re-scaled fields, changed semantics)
- MINOR: a new capability - an RPC, a logged quantity, a device
- PATCH: fixes and refinements that change nothing about what is
  recorded

Bump in the same commit as the change it describes. A pydase server's
number is the ``__version__`` of its server.py; logger2's is the
``[project]`` version of its pyproject.toml. The git commit of the
checkout, with a "+dirty" marker when tracked files are modified, is
recorded beside it: version numbers are for humans, the commit is for
forensics.

Everything is captured ONCE at startup (`capture_versions`): a
``git pull`` under a running process changes the checkout, not the
running code, so the values describe the process until its restart.

Two hazards shape the git probe. The Windows services run as
LocalSystem while the checkouts belong to the Unitrap user, and git
refuses a repository owned by another account ("dubious ownership")
unless ``safe.directory`` names it, so every call names it on the
command line; when git cannot run at all, the hash is read from the
.git directory itself, without the dirty check. And a running pydase
server rewrites its tracked state.json, which would mark the checkout
dirty forever, so those files are excluded from the check.

This module is identical in unitrap-pydase-apps and logger2 (diff
them). It imports nothing beyond the standard library and has no
import-time side effects.
"""

import importlib.metadata
import logging
import re
import subprocess
import sys
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

#: Field names on the health point (the ion-detection Run event uses
#: the same two for the GUI's own software)
FIELD_VERSION = 'software_version'
FIELD_COMMIT = 'software_commit'
#: Bound on one git call (s); a startup waits for at most two
GIT_TIMEOUT_S = 5.0
#: Tracked files a running process rewrites; they never make a
#: checkout dirty
DIRTY_EXCLUDE_PATHSPECS = (':(exclude,glob)**/state.json',)
#: Distributions whose installed versions are published beside the
#: process's own, as '<distribution>_version'
DEPENDENCIES = ('pydase', 'amodevices')
#: Length of a hash read from the .git directory, matching what
#: `git rev-parse --short` prints for these repositories
SHORT_HASH_LEN = 7
#: The shape a version is expected to have
VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')
# A full SHA-1 or SHA-256 object name
_HASH_RE = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
# No console window flashing up from a process that has none (a service)
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


class GitProbeError(Exception):
    """git could not answer: it is missing, timed out, or refused."""


def run_git(args, repo_root, timeout_s=GIT_TIMEOUT_S):
    """Run one git command in `repo_root` and return its stdout.

    The only place git is invoked. Raises `GitProbeError` carrying
    git's first stderr line on a nonzero exit (so "detected dubious
    ownership" reaches the log), and the exception's name when git
    could not run at all.
    """
    root = Path(repo_root)
    cmd = ['git', '-c', 'safe.directory=' + root.as_posix(),
           '--no-optional-locks', *args]
    try:
        result = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=timeout_s, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise GitProbeError(f'{type(e).__name__}: {e}') from e
    if result.returncode != 0:
        lines = (result.stderr or '').strip().splitlines()
        raise GitProbeError(
            lines[0] if lines else f'exit status {result.returncode}')
    return result.stdout


def commit_from_git(repo_root):
    """The short HEAD hash, "+dirty"-suffixed when tracked files other
    than `DIRTY_EXCLUDE_PATHSPECS` are modified.

    Raises `GitProbeError` when the hash itself cannot be read; a dirty
    check that fails keeps the bare hash and warns.
    """
    commit = run_git(['rev-parse', '--short', 'HEAD'], repo_root).strip()
    if not commit:
        raise GitProbeError('rev-parse printed nothing')
    try:
        status = run_git(
            ['status', '--porcelain', '--untracked-files=no', '--', '.',
             *DIRTY_EXCLUDE_PATHSPECS], repo_root)
    except GitProbeError as e:
        logger.warning(
            'The dirty check failed in \'%s\' (%s); the commit is published'
            ' without it', repo_root, e)
        return commit
    if status.strip():
        commit += '+dirty'
    return commit


def commit_from_git_files(repo_root):
    """The short HEAD hash read from the .git directory itself, for a
    process that cannot run git.

    None when the layout is not understood (a worktree's .git file, an
    unborn branch). Carries no dirty information: that needs git.
    """
    git_dir = Path(repo_root) / '.git'
    try:
        head = (git_dir / 'HEAD').read_text(encoding='utf-8').strip()
        if head.startswith('ref:'):
            ref = head[4:].strip()
            loose = git_dir.joinpath(*ref.split('/'))
            if loose.is_file():
                head = loose.read_text(encoding='utf-8').strip()
            else:
                head = _packed_ref(git_dir / 'packed-refs', ref)
        if head is None or not _HASH_RE.match(head):
            return None
        return head[:SHORT_HASH_LEN]
    except Exception:
        return None


def _packed_ref(packed_refs, ref):
    """The object name `ref` resolves to in a packed-refs file, or None."""
    if not packed_refs.is_file():
        return None
    for line in packed_refs.read_text(encoding='utf-8').splitlines():
        # '#' opens the header, '^' a peeled tag's target
        if not line or line[0] in '#^':
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return parts[0]
    return None


def capture_commit(repo_root):
    """The checkout's commit via git, else via the .git directory; None
    with a WARNING when neither works. Never raises."""
    try:
        root = Path(repo_root)
        if not (root / '.git').exists():
            logger.warning(
                '\'%s\' is not a git checkout; no commit is published', root)
            return None
        try:
            return commit_from_git(root)
        except GitProbeError as e:
            commit = commit_from_git_files(root)
            if commit is None:
                logger.warning(
                    'Could not read the commit of \'%s\': git could not run'
                    ' (%s) and the .git directory was not understood',
                    root, e)
                return None
            logger.warning(
                'git could not run in \'%s\' (%s); the commit was read from'
                ' the .git directory, without the dirty check', root, e)
            return commit
    except Exception as e:
        logger.warning('Could not read the commit of \'%s\': %s', repo_root, e)
        return None


def installed_version(distribution):
    """The installed version of `distribution`, None when it is not
    installed."""
    try:
        return str(importlib.metadata.version(distribution))
    except Exception:
        return None


def main_module_version():
    """The `__version__` of the running script, None with a WARNING when
    it defines none (the pydase servers)."""
    main = sys.modules.get('__main__')
    value = getattr(main, '__version__', None)
    if value is None:
        logger.warning(
            '\'%s\' defines no __version__; the %s field stays absent',
            getattr(main, '__file__', '__main__'), FIELD_VERSION)
        return None
    return str(value)


def version_from_pyproject(repo_root):
    """The `[project]` version of `<repo_root>/pyproject.toml`, None with
    a WARNING when the file or the key is missing (logger2)."""
    path = Path(repo_root) / 'pyproject.toml'
    try:
        with open(path, 'rb') as f:
            return str(tomllib.load(f)['project']['version'])
    except Exception as e:
        logger.warning(
            'No version in \'%s\' (%s); the %s field stays absent',
            path, e, FIELD_VERSION)
        return None


def capture_versions(app_version, repo_root, dependencies=DEPENDENCIES):
    """Everything published about the running software, as string
    fields.

    `FIELD_VERSION` is `app_version` as given (from
    `main_module_version` or `version_from_pyproject`, which warn when
    they find none), `FIELD_COMMIT` comes from `capture_commit`, and
    '<distribution>_version' is added for each of `dependencies` that
    is installed. An unknown value is ABSENT, never an empty string,
    and costs one WARNING. Never raises.
    """
    fields = {}
    if app_version is not None:
        version = str(app_version).strip()
        if not VERSION_RE.match(version):
            logger.warning(
                'The version \'%s\' is not of the form MAJOR.MINOR.PATCH;'
                ' published as given', version)
        fields[FIELD_VERSION] = version
    commit = capture_commit(repo_root)
    if commit is not None:
        fields[FIELD_COMMIT] = commit
    for name in dependencies:
        version = installed_version(name)
        if version is None:
            logger.warning(
                'The \'%s\' distribution is not installed; its version field'
                ' stays absent', name)
        else:
            fields[f'{name}_version'] = version
    return fields


def version_string(fields):
    """E.g. "1.2.0 (abc1234+dirty)"; "unknown" without a version."""
    version = fields.get(FIELD_VERSION) or 'unknown'
    commit = fields.get(FIELD_COMMIT)
    return version + (f' ({commit})' if commit else '')


def describe(fields, dependencies=DEPENDENCIES):
    """E.g. "1.2.0 (abc1234), pydase 0.10.21, amodevices 0.1.23" - the
    tail of the startup log line; unknown entries are left out."""
    parts = [version_string(fields)]
    for name in dependencies:
        version = fields.get(f'{name}_version')
        if version:
            parts.append(f'{name} {version}')
    return ', '.join(parts)
