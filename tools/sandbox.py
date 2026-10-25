"""Offline Linux execution for stopped candidate exports; no host execution.

Operator API: grade_candidate(candidate, case_id, confirmed_stopped=True).
Run as any non-root Linux user. The caller must first prove
all model/agent writers stopped; this module cannot establish that fact itself.
Only manifest allowed_files and optional BRIEF.md enter the export. The API
defaults to strict export; the CLI defaults to functional export and accepts
--strict-export to reject source extras. Functional mode never visits extras.

The complete private grader runs inside an outer bwrap namespace, on a sized
tmpfs. Its runner starts a NEW nested bwrap PID/mount/network namespace per
call, exposing only /usr, fixed minimal /etc, /workspace, /tmp and synthetic
/proc,/dev. Thus candidate descendants cannot see/signal the grader or read its
source. Neither layer mounts host homes, /mnt, /opt, sockets, or host /proc.
No fallback to host execution exists. Requires Linux bwrap and prlimit.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


BASE = Path(__file__).resolve().parents[1]
CASES = frozenset([f'F{i:02d}' for i in range(1, 9)] + ['R07', 'R08'])
MAX_EXPORT = 4 * 1024 * 1024
MAX_FILE = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    seconds: float = 15
    cpu_seconds: int = 12
    memory_bytes: int = 512 * 1024 * 1024
    processes: int = 32
    file_bytes: int = 8 * 1024 * 1024
    output_bytes: int = 65536
    workspace_bytes: int = 64 * 1024 * 1024

    def __post_init__(self):
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in vars(self).values()):
            raise ValueError('limits must be positive numbers')
        if any(type(v) is not int for k, v in vars(self).items() if k != 'seconds'):
            raise ValueError('resource limits must be integer values')
        if self.output_bytes > 65536:
            raise ValueError('oracle output cap is 65536 bytes per stream')


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limited: bool = False


def require_linux_user():
    if sys.platform != 'linux':
        raise RuntimeError('Linux is required; host execution is forbidden')
    if os.getuid() == 0:
        raise RuntimeError('run as a non-root Linux user')


def _path(name):
    p = PurePosixPath(name)
    if not p.parts or '\x00' in name or p.is_absolute() or '\\' in name or str(p) != name or any(
            part in ('.', '..') for part in p.parts):
        raise ValueError('noncanonical export path')
    return p


def _directory_fd(path):
    """Open each ancestor without following links, including the source root."""
    absolute = Path(path).absolute()
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _export_identity(info):
    # Reading may itself update atime. All mutation/identity metadata remains
    # checked at nanosecond precision; access time is not a candidate write.
    return tuple(getattr(info, field) for field in
                 ('st_mtime_ns', 'st_ctime_ns', 'st_ino', 'st_dev', 'st_nlink', 'st_mode', 'st_size'))


def export_candidate(source, destination, allowed_files, *, functional_source_only=False):
    """Copy bounded bytes using dirfds/O_NOFOLLOW; refuse extras, links, devices.

    destination must be a newly created operator-owned empty directory. No
    metadata/permissions from the candidate are copied; hardlinks are refused.
    Rejecting every extra file also prevents sitecustomize and shadow modules.
    Functional mode instead opens only the registered paths and optional BRIEF;
    unlisted artifacts are neither inspected, copied nor executed.
    """
    if type(functional_source_only) is not bool:
        raise ValueError('functional_source_only must be boolean')
    if sys.platform != 'linux':
        raise RuntimeError('secure dirfd export requires Linux')
    allowed = {_path(name).as_posix() for name in allowed_files}
    if not allowed or len(allowed) > 64:
        raise ValueError('invalid allowed_files')
    accepted = allowed | {'BRIEF.md'}
    directories = {p.as_posix() for name in accepted for p in _path(name).parents if str(p) != '.'}
    dest = Path(destination)
    if not dest.is_dir() or any(dest.iterdir()):
        raise ValueError('export destination must be empty')
    copied, total, count = {}, 0, 0

    def copy_file(fd, name, relative):
        nonlocal total
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE:
                raise ValueError('nonregular/oversized export file')
            data = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
        if _export_identity(before) != _export_identity(after) or len(data) > MAX_FILE:
            raise ValueError('export changed during copy')
        total += len(data)
        if total > MAX_EXPORT:
            raise ValueError('export size limit exceeded')
        (dest / relative).write_bytes(data)
        copied[relative] = hashlib.sha256(data).hexdigest()

    def walk(fd, prefix=''):
        nonlocal total, count
        for name in sorted(os.listdir(fd)):
            count += 1
            relative = prefix + name
            if count > 128 or len(_path(relative).parts) > 8:
                raise ValueError('export inventory limit exceeded')
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode) and relative in directories:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    (dest / relative).mkdir(mode=0o700)
                    walk(child, relative + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and relative in accepted and info.st_nlink == 1:
                copy_file(fd, name, relative)
            else:
                raise ValueError('unexpected file, link, or special entry: ' + relative)

    def selected_paths(root_fd):
        selected = set()
        for relative in sorted(accepted):
            parts = _path(relative).parts
            if len(parts) > 8:
                raise ValueError('export inventory limit exceeded')
            fd = os.dup(root_fd)
            try:
                for index, part in enumerate(parts[:-1], 1):
                    parent = '/'.join(parts[:index])
                    selected.add(parent)
                    if len(selected) > 128:
                        raise ValueError('export inventory limit exceeded')
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    (dest / parent).mkdir(mode=0o700, exist_ok=True)
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                selected.add(relative)
                if len(selected) > 128:
                    raise ValueError('export inventory limit exceeded')
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('nonregular/linked export file: ' + relative)
                copy_file(fd, parts[-1], relative)
            except FileNotFoundError:
                if relative != 'BRIEF.md' or relative in allowed:
                    raise ValueError('required candidate file missing: ' + relative) from None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError('unsafe candidate source path: ' + relative) from None
                raise  # Unexpected host I/O failure is not a candidate quality verdict.
            finally:
                os.close(fd)

    try:
        root_fd = _directory_fd(source)
    except OSError as exc:
        if functional_source_only and exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError('unsafe candidate source directory') from None
        raise
    try:
        if functional_source_only:
            selected_paths(root_fd)
        else:
            walk(root_fd)
    finally:
        os.close(root_fd)
    if not allowed <= copied.keys():
        raise ValueError('required candidate file missing')
    return copied


def bwrap_argv(workspace, limits=Limits(), *, grading=None):
    """Return explicit argv; grading=(seed, trusted_payload) adds outer mounts."""
    argv = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--die-with-parent', '--new-session',
            '--cap-drop', 'ALL', '--clearenv', '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/sbin', '/sbin',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--dir', '/etc', '--dir', '/home', '--dir', '/home/runner',
            '--proc', '/proc', '--dev', '/dev', '--size', str(16 * 1024 * 1024),
            '--tmpfs', '/tmp']
    # Fixed nonsecret resolver-free runtime files; do not expose /etc wholesale.
    for name in ('ld.so.cache', 'localtime'):
        if (Path('/etc') / name).is_file():
            argv += ['--ro-bind', '/etc/' + name, '/etc/' + name]
    if grading is None:
        # Candidate must not create another user namespace to regain mount caps
        # and manufacture unlimited tmpfs mounts. Outer grader needs nesting.
        argv += ['--disable-userns', '--bind', str(workspace), '/workspace']
    else:
        seed, trusted = grading
        argv += ['--ro-bind', str(seed), '/seed', '--ro-bind', str(trusted), '/grader',
                 '--size', str(limits.workspace_bytes), '--tmpfs', '/workspace']
    # bwrap's default root/dev tmpfs are writable and otherwise evade the sized
    # /workspace and /tmp mounts. Device nodes remain usable on readonly /dev.
    argv += ['--remount-ro', '/', '--remount-ro', '/dev',
             '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/home/runner',
             '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
             '--chdir', '/workspace', '--']
    return argv


def limited_argv(argv, limits):
    return ['/usr/bin/prlimit', f'--as={limits.memory_bytes}',
            f'--cpu={limits.cpu_seconds}', f'--nproc={limits.processes}',
            f'--fsize={limits.file_bytes}', '--nofile=96', '--core=0', '--', *argv]


def uid_thread_baseline(proc_root=Path('/proc'), *, uid=None):
    """Count real-UID Linux threads: RLIMIT_NPROC includes other UID services.

    Sample once OUTSIDE the grader PID namespace, then retain that allowance in
    every nested stage. Recomputing inside sees only that namespace's processes
    and undercounts concurrent Herdr/sshd/OpenCode threads of the same real UID.
    The count is a snapshot; independently starting UID services consume margin.
    """
    uid = os.getuid() if uid is None else uid
    total = 0
    for process in Path(proc_root).iterdir():
        if not process.name.isdigit():
            continue
        try:
            fields = dict(line.split(':', 1) for line in (process / 'status').read_text().splitlines()
                          if ':' in line)
            if int(fields['Uid'].split()[0]) == uid:
                total += int(fields['Threads'])
        except (OSError, KeyError, ValueError, IndexError):
            continue  # process exited during the snapshot, or a different UID
    if total < 1:
        raise RuntimeError('cannot establish real-UID thread baseline')
    return total


def run_bounded(argv, limits=Limits()):
    """Drain both pipes with bounded memory, kill namespace on limit or timeout."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, close_fds=True)
    streams = [bytearray(), bytearray()]
    timed_out = overflow = False
    deadline = time.monotonic() + limits.seconds
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, 0)
        selector.register(process.stderr, selectors.EVENT_READ, 1)
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    output = streams[key.data]
                    room = limits.output_bytes - len(output)
                    output.extend(data[:room])
                    if len(data) > room:
                        overflow = True
                        break
                if overflow:
                    break
            # A child may close both pipes then continue computing.
            if not timed_out and not overflow:
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
        finally:
            # bwrap --die-with-parent + private PID init kills every descendant,
            # including setsid/double-fork children, before workspace inspection.
            # Once wait() has reaped the child its PID may be reused. Only
            # signal our unreaped process group; successful bwrap exit already
            # destroyed the private PID namespace and its remaining writers.
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
    code = 124 if timed_out else 125 if overflow else process.returncode
    return Result(code, bytes(streams[0]), bytes(streams[1]), timed_out, overflow)


def _inside_grade(case_id):
    """Trusted entrypoint; invoked only by outer bwrap using Python -I."""
    import runpy
    shutil.copytree('/seed', '/workspace', dirs_exist_ok=True)
    # Created by the trusted outer controller before its namespace was entered.
    limits = Limits(**json.loads(Path('/grader/limits.json').read_text()))

    def runner(argv, *, cwd):
        if Path(cwd).absolute() != Path('/workspace'):
            raise ValueError('runner cwd must be exported workspace')
        if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) and '\x00' not in a for a in argv):
            raise ValueError('invalid Python argv')
        command = bwrap_argv('/workspace', limits) + ['/usr/bin/python3', '-B', '-s', *argv]
        return run_bounded(limited_argv(command, limits), limits)

    # An unavailable nested sandbox is infrastructure failure, never a failed
    # model answer. -I keeps this trusted readiness probe independent of source.
    probe = runner(['-I', '-c', 'from pathlib import Path; '
                    'assert not Path("/grader").exists(); print("LOCAL_MODEL_SANDBOX_OK")'],
                   cwd=Path('/workspace'))
    if probe.returncode or probe.stdout != b'LOCAL_MODEL_SANDBOX_OK\n':
        raise RuntimeError('nested sandbox preflight failed: ' + repr(probe.stderr))
    oracle = runpy.run_path('/grader/oracle.py')
    result = oracle['grade'](Path('/workspace'), runner)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))


def grade_candidate(candidate, case_id, *, confirmed_stopped=False, base=BASE,
                    functional_source_only=False):
    """Return verdict, export digests and isolation receipt. No model is invoked.

    Caller supplies the trusted repository campaign root, never a model-selected
    path. Oracle SHA must match the registered case manifest. Invalid exports
    fail before any execution; sandbox failure is infrastructure, not model FAIL.
    """
    if type(functional_source_only) is not bool:
        raise ValueError('functional_source_only must be boolean')
    require_linux_user()
    if not confirmed_stopped:
        raise ValueError('confirmed stop of all agent writers is required')
    if case_id not in CASES:
        raise ValueError('unregistered case')
    base = Path(base)
    manifest = json.loads((base / 'cases' / (case_id + '.json')).read_text(encoding='utf-8'))
    oracle = (base / 'criteria' / (case_id + '.py')).read_bytes()
    oracle_sha = hashlib.sha256(oracle).hexdigest()
    if manifest['case_id'] != case_id or oracle_sha != manifest['oracle_sha']:
        raise ValueError('oracle manifest SHA mismatch')
    baseline = uid_thread_baseline()
    nproc = baseline + 32
    candidate_limits = Limits(processes=nproc)
    with tempfile.TemporaryDirectory(prefix='local-model-grade-') as temporary:
        root = Path(temporary)
        seed, trusted = root / 'seed', root / 'trusted'
        seed.mkdir(mode=0o700)
        trusted.mkdir(mode=0o700)
        digests = export_candidate(candidate, seed, manifest['allowed_files'],
                                   functional_source_only=functional_source_only)
        (trusted / 'oracle.py').write_bytes(oracle)
        (trusted / 'runner.py').write_bytes(Path(__file__).read_bytes())
        (trusted / 'limits.json').write_text(json.dumps(vars(candidate_limits)))
        limits = Limits(seconds=240, cpu_seconds=180, processes=nproc)
        command = bwrap_argv('/workspace', limits, grading=(seed, trusted)) + [
            '/usr/bin/python3', '-I', '-B', '/grader/runner.py', '_inside-grade', case_id]
        result = run_bounded(limited_argv(command, limits), limits)
        receipt = {'case_id': case_id, 'oracle_sha256': oracle_sha, 'export_sha256': digests,
                   'export_policy': 'functional-source-v1' if functional_source_only else 'strict-v1',
                   'isolation': 'bwrap-nested-offline', 'returncode': result.returncode,
                   'timed_out': result.timed_out, 'output_limited': result.output_limited,
                   'argv': command, 'candidate_limits': vars(candidate_limits),
                   'nproc_limit_scope': 'real UID threads across all namespaces; sampled baseline + 32 headroom',
                   'uid_thread_baseline': baseline, 'uid_thread_headroom': 32,
                   'uid_thread_limit': nproc,
                   'memory_limit_scope': 'per-process RLIMIT_AS; no cgroup aggregate quota'}
        if result.returncode != 0:
            return {'status': 'INFRASTRUCTURE_ERROR', 'receipt': receipt,
                    'stderr': result.stderr.decode('utf-8', errors='replace')}
        try:
            verdict = json.loads(result.stdout)
            if verdict['case_id'] != case_id or verdict['verdict'] not in ('PASS', 'FAIL'):
                raise ValueError('invalid oracle result')
        except (ValueError, KeyError, TypeError):
            return {'status': 'INFRASTRUCTURE_ERROR', 'receipt': receipt, 'stderr': 'invalid grader output'}
        return {'status': 'GRADED', 'result': verdict, 'receipt': receipt}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '_inside-grade':
        _inside_grade(sys.argv[2])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_id', choices=sorted(CASES))
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--confirmed-stopped', action='store_true')
    parser.add_argument('--strict-export', action='store_true',
                        help='reject files outside the registered sources and optional BRIEF.md')
    args = parser.parse_args()
    try:
        result = grade_candidate(args.candidate, args.case_id,
                                 confirmed_stopped=args.confirmed_stopped,
                                 functional_source_only=not args.strict_export)
    except (OSError, ValueError, RuntimeError) as exc:
        print('sandbox: ' + str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] != 'GRADED':
        raise SystemExit(2)
    raise SystemExit(0 if result['result']['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
