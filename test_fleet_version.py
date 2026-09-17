# -*- coding: utf-8 -*-
"""Tests for `fleet_version`: the version, commit and dependency
versions a process publishes about itself.

git is faked through `subprocess.run`, except in the one test that
proves the pathspec semantics on a real repository. Identical in
unitrap-pydase-apps and logger2, like the module.
"""

import importlib.metadata
import logging
import shutil
import subprocess
import sys

import pytest

import fleet_version as fv

FULL_HASH = '0123456789abcdef0123456789abcdef01234567'
OTHER_HASH = 'fedcba9876543210fedcba9876543210fedcba98'


class FakeGit:
    """A `subprocess.run` stand-in answering git from a script: `rev`
    for rev-parse, `status` for status; `fail` (an exception to raise,
    or a (returncode, stderr) pair) for every call, `status_fail` for
    the status call only."""

    def __init__(self, rev='abc1234', status='', fail=None,
                 status_fail=None):
        self.calls = []
        self.rev = rev
        self.status = status
        self.fail = fail
        self.status_fail = status_fail

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        failure = self.fail
        if 'status' in cmd and self.status_fail is not None:
            failure = self.status_fail
        if isinstance(failure, BaseException):
            raise failure
        if failure is not None:
            code, stderr = failure
            return subprocess.CompletedProcess(cmd, code, '', stderr)
        if 'rev-parse' in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.rev + '\n', '')
        if 'status' in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.status, '')
        raise AssertionError('unexpected git call %r' % (cmd,))


@pytest.fixture
def checkout(tmp_path):
    """A directory shaped like a checkout on `main` at `FULL_HASH`,
    with a loose ref file."""
    git = tmp_path / '.git'
    (git / 'refs' / 'heads').mkdir(parents=True)
    (git / 'HEAD').write_text('ref: refs/heads/main\n')
    (git / 'refs' / 'heads' / 'main').write_text(FULL_HASH + '\n')
    return tmp_path


@pytest.fixture
def fake_git(monkeypatch):
    def install(**kwargs):
        fake = FakeGit(**kwargs)
        monkeypatch.setattr(subprocess, 'run', fake)
        return fake
    return install


# -- the git calls ---------------------------------------------------


def test_git_is_called_with_safe_directory_and_no_locks(checkout, fake_git):
    """The Windows services run as LocalSystem in a checkout the
    Unitrap user owns; without `safe.directory` git refuses it."""
    fake = fake_git()
    assert fv.commit_from_git(checkout) == 'abc1234'
    cmd, kwargs = fake.calls[0]
    assert cmd == ['git', '-c', 'safe.directory=' + checkout.as_posix(),
                   '--no-optional-locks', 'rev-parse', '--short', 'HEAD']
    assert kwargs['cwd'] == str(checkout)
    assert kwargs['timeout'] == fv.GIT_TIMEOUT_S
    assert kwargs['capture_output'] and kwargs['text']
    if sys.platform == 'win32':
        assert kwargs['creationflags'] == subprocess.CREATE_NO_WINDOW


def test_dirty_check_excludes_state_json(checkout, fake_git):
    """A running pydase server rewrites its tracked state.json; that
    must never make the checkout dirty."""
    fake = fake_git()
    fv.commit_from_git(checkout)
    cmd, _ = fake.calls[1]
    assert 'status' in cmd and '--porcelain' in cmd
    assert cmd[-4:] == ['--untracked-files=no', '--', '.',
                        ':(exclude,glob)**/state.json']


def test_clean_checkout_is_the_bare_hash(checkout, fake_git):
    fake_git(status='')
    assert fv.commit_from_git(checkout) == 'abc1234'


def test_modified_tracked_file_marks_dirty(checkout, fake_git):
    fake_git(status=' M server.py\n')
    assert fv.commit_from_git(checkout) == 'abc1234+dirty'


def test_failed_dirty_check_keeps_the_hash(checkout, fake_git, caplog):
    fake_git(status_fail=(128, 'fatal: Unable to create index.lock'))
    with caplog.at_level(logging.WARNING):
        assert fv.commit_from_git(checkout) == 'abc1234'
    assert 'dirty check failed' in caplog.text
    assert 'index.lock' in caplog.text


def test_empty_rev_parse_is_a_probe_error(checkout, fake_git):
    fake_git(rev='')
    with pytest.raises(fv.GitProbeError):
        fv.commit_from_git(checkout)


@pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')
def test_real_git_ignores_state_json_but_not_code(tmp_path):
    """The one test that proves git's pathspec semantics."""
    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True,
                       capture_output=True, text=True)

    git('init', '-q')
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'Test')
    git('config', 'commit.gpgsign', 'false')
    (tmp_path / 'pid').mkdir()
    (tmp_path / 'pid' / 'state.json').write_text('{}')
    (tmp_path / 'server.py').write_text('x = 1')
    git('add', '.')
    git('commit', '-q', '-m', 'seed')
    commit = fv.commit_from_git(tmp_path)
    assert len(commit) >= fv.SHORT_HASH_LEN and '+dirty' not in commit
    (tmp_path / 'pid' / 'state.json').write_text('{"a": 1}')
    assert fv.commit_from_git(tmp_path) == commit
    (tmp_path / 'server.py').write_text('x = 2')
    assert fv.commit_from_git(tmp_path) == commit + '+dirty'


# -- the .git directory fallback -------------------------------------


@pytest.mark.parametrize('failure', [
    FileNotFoundError('git'),
    subprocess.TimeoutExpired('git', 5),
    (128, 'fatal: detected dubious ownership in repository at ...'),
    ], ids=['missing', 'timeout', 'refused'])
def test_git_failure_falls_back_to_the_git_directory(
        checkout, fake_git, caplog, failure):
    fake_git(fail=failure)
    with caplog.at_level(logging.WARNING):
        assert fv.capture_commit(checkout) == FULL_HASH[:fv.SHORT_HASH_LEN]
    assert 'without the dirty check' in caplog.text
    reason = ('dubious ownership' if isinstance(failure, tuple)
              else type(failure).__name__)
    assert reason in caplog.text


def test_loose_ref_is_read(checkout):
    assert fv.commit_from_git_files(checkout) == FULL_HASH[:fv.SHORT_HASH_LEN]


def test_packed_ref_is_read(checkout):
    (checkout / '.git' / 'refs' / 'heads' / 'main').unlink()
    (checkout / '.git' / 'packed-refs').write_text(
        '# pack-refs with: peeled fully-peeled sorted \n'
        + OTHER_HASH + ' refs/heads/dev\n'
        + FULL_HASH + ' refs/heads/main\n'
        + '^' + OTHER_HASH + '\n')
    assert fv.commit_from_git_files(checkout) == FULL_HASH[:fv.SHORT_HASH_LEN]


def test_detached_head_is_read(checkout):
    (checkout / '.git' / 'HEAD').write_text(OTHER_HASH + '\n')
    assert fv.commit_from_git_files(checkout) == OTHER_HASH[:fv.SHORT_HASH_LEN]


def test_unborn_branch_is_not_understood(checkout):
    (checkout / '.git' / 'refs' / 'heads' / 'main').unlink()
    assert fv.commit_from_git_files(checkout) is None


def test_garbage_in_head_is_not_understood(checkout):
    (checkout / '.git' / 'HEAD').write_text('not a hash\n')
    assert fv.commit_from_git_files(checkout) is None


def test_both_failing_means_no_commit_and_a_warning(
        checkout, fake_git, caplog):
    fake_git(fail=FileNotFoundError('git'))
    (checkout / '.git' / 'HEAD').write_text('not a hash\n')
    with caplog.at_level(logging.WARNING):
        assert fv.capture_commit(checkout) is None
    assert 'not understood' in caplog.text


def test_no_checkout_means_no_commit_and_no_git_call(
        tmp_path, fake_git, caplog):
    fake = fake_git()
    with caplog.at_level(logging.WARNING):
        assert fv.capture_commit(tmp_path) is None
    assert not fake.calls
    assert 'not a git checkout' in caplog.text


def test_an_unexpected_error_is_contained(checkout, fake_git, caplog):
    """Nothing here may raise into a process's startup."""
    fake_git(fail=RuntimeError('boom'))
    with caplog.at_level(logging.WARNING):
        assert fv.capture_commit(checkout) is None
    assert 'boom' in caplog.text


# -- the version sources ---------------------------------------------


def test_main_module_version_reads_the_running_script(monkeypatch):
    monkeypatch.setattr(sys.modules['__main__'], '__version__', '1.2.3',
                        raising=False)
    assert fv.main_module_version() == '1.2.3'


def test_main_module_without_version_warns(monkeypatch, caplog):
    monkeypatch.delattr(sys.modules['__main__'], '__version__',
                        raising=False)
    with caplog.at_level(logging.WARNING):
        assert fv.main_module_version() is None
    assert 'defines no __version__' in caplog.text


def test_version_from_pyproject(tmp_path):
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname = "demo"\nversion = "2.0.0"\n')
    assert fv.version_from_pyproject(tmp_path) == '2.0.0'


@pytest.mark.parametrize('content', [
    None, '[project]\nname = "demo"\n', '[project\nbroken',
    ], ids=['missing file', 'missing key', 'malformed'])
def test_pyproject_without_a_version_warns(tmp_path, caplog, content):
    if content is not None:
        (tmp_path / 'pyproject.toml').write_text(content)
    with caplog.at_level(logging.WARNING):
        assert fv.version_from_pyproject(tmp_path) is None
    assert 'No version in' in caplog.text


def test_installed_version_matches_importlib():
    assert fv.installed_version('pytest') == importlib.metadata.version(
        'pytest')


def test_uninstalled_distribution_has_no_version():
    assert fv.installed_version('no-such-distribution-xyz') is None


# -- capture_versions ------------------------------------------------


def test_every_field_is_a_string(checkout, fake_git):
    fake_git()
    fields = fv.capture_versions('1.2.3', checkout, dependencies=('pytest',))
    assert set(fields) == {fv.FIELD_VERSION, fv.FIELD_COMMIT, 'pytest_version'}
    assert fields[fv.FIELD_VERSION] == '1.2.3'
    assert fields[fv.FIELD_COMMIT] == 'abc1234'
    for key, value in fields.items():
        assert type(value) is str, key


def test_unknown_values_are_absent_not_empty(tmp_path, fake_git, caplog):
    fake_git()
    with caplog.at_level(logging.WARNING):
        fields = fv.capture_versions(
            None, tmp_path, dependencies=('no-such-distribution-xyz',))
    assert fields == {}
    assert 'no-such-distribution-xyz' in caplog.text


def test_an_odd_version_is_published_with_a_warning(
        checkout, fake_git, caplog):
    fake_git()
    with caplog.at_level(logging.WARNING):
        fields = fv.capture_versions(' 1.2 ', checkout, dependencies=())
    assert fields[fv.FIELD_VERSION] == '1.2'
    assert 'MAJOR.MINOR.PATCH' in caplog.text


def test_a_non_string_version_is_stringified(checkout, fake_git):
    fake_git()
    fields = fv.capture_versions(1.0, checkout, dependencies=())
    assert fields[fv.FIELD_VERSION] == '1.0'
    assert type(fields[fv.FIELD_VERSION]) is str


# -- the strings -----------------------------------------------------


@pytest.mark.parametrize('fields, expected', [
    ({fv.FIELD_VERSION: '1.2.0', fv.FIELD_COMMIT: 'abc1234+dirty'},
     '1.2.0 (abc1234+dirty)'),
    ({fv.FIELD_VERSION: '1.2.0'}, '1.2.0'),
    ({fv.FIELD_COMMIT: 'abc1234'}, 'unknown (abc1234)'),
    ({}, 'unknown'),
    ])
def test_version_string(fields, expected):
    assert fv.version_string(fields) == expected


def test_describe_lists_the_dependencies_in_order():
    fields = {fv.FIELD_VERSION: '1.2.0', fv.FIELD_COMMIT: 'abc1234',
              'amodevices_version': '0.1.23', 'pydase_version': '0.10.21'}
    assert fv.describe(fields) == (
        '1.2.0 (abc1234), pydase 0.10.21, amodevices 0.1.23')


def test_describe_leaves_unknown_dependencies_out():
    fields = {fv.FIELD_VERSION: '1.2.0', 'amodevices_version': '0.1.23'}
    assert fv.describe(fields) == '1.2.0, amodevices 0.1.23'
