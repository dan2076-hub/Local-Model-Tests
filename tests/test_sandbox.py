"""Portable argv checks plus Linux integration tests; never run candidate on host.

Windows: python -B -m unittest discover -s tests -p test_sandbox.py -v
Linux: python3 -B tests/test_sandbox.py, as any non-root user.
Linux integration failures are failures, including unavailable nested namespaces.
"""
import dataclasses
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import sandbox


LINUX = sys.platform == 'linux'


class CommandTests(unittest.TestCase):
    def test_cli_verdict_exit_codes_and_export_policy(self):
        outcomes = [('GRADED', 'PASS', 0), ('GRADED', 'FAIL', 1),
                    ('INFRASTRUCTURE_ERROR', None, 2)]
        for strict in (False, True):
            for status, verdict, code in outcomes:
                result = {'status': status, 'result': {'verdict': verdict}}
                argv = ['sandbox.py', 'F01', '/not-accessed', '--confirmed-stopped']
                if strict:
                    argv.append('--strict-export')
                with self.subTest(strict=strict, status=status, verdict=verdict), \
                     patch.object(sys, 'argv', argv), \
                     patch.object(sandbox, 'grade_candidate', return_value=result) as grade, \
                     patch.object(sys, 'stdout', io.StringIO()), \
                     self.assertRaises(SystemExit) as raised:
                    sandbox.main()
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(grade.call_args.kwargs['functional_source_only'], not strict)
                self.assertIs(grade.call_args.kwargs['confirmed_stopped'], True)

    def test_cli_platform_and_validation_errors_have_no_traceback(self):
        for error in (RuntimeError('Linux required'), ValueError('invalid export'), OSError('missing')):
            with self.subTest(error=type(error).__name__), \
                 patch.object(sys, 'argv', ['sandbox.py', 'F01', '/not-accessed', '--confirmed-stopped']), \
                 patch.object(sandbox, 'grade_candidate', side_effect=error), \
                 patch.object(sys, 'stderr', io.StringIO()) as stderr, \
                 self.assertRaises(SystemExit) as raised:
                sandbox.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(str(error), stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())

    def test_linux_policy_allows_any_nonroot_uid_and_rejects_root(self):
        with patch.object(sandbox.sys, 'platform', 'linux'), \
             patch.object(sandbox.os, 'getuid', return_value=4321, create=True):
            sandbox.require_linux_user()
        with patch.object(sandbox.sys, 'platform', 'linux'), \
             patch.object(sandbox.os, 'getuid', return_value=0, create=True), \
             self.assertRaisesRegex(RuntimeError, 'non-root'):
            sandbox.require_linux_user()
        with patch.object(sandbox.sys, 'platform', 'win32'), \
             self.assertRaisesRegex(RuntimeError, 'Linux'):
            sandbox.require_linux_user()

    def test_functional_export_does_not_convert_host_io_failure_into_quality_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sandbox.sys, 'platform', 'linux'), \
                 patch.object(sandbox, '_directory_fd', side_effect=OSError(errno.EIO, 'synthetic host I/O')):
                with self.assertRaises(OSError) as error:
                    sandbox.export_candidate('/not-accessed', tmp, ['main.py'], functional_source_only=True)
                self.assertEqual(error.exception.errno, errno.EIO)
            with patch.object(sandbox.sys, 'platform', 'linux'), \
                 patch.object(sandbox, '_directory_fd', side_effect=OSError(errno.ELOOP, 'synthetic link')):
                with self.assertRaisesRegex(ValueError, 'unsafe candidate source'):
                    sandbox.export_candidate('/not-accessed', tmp, ['main.py'], functional_source_only=True)

    def test_functional_grading_routes_export_policy_without_executing_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'cases').mkdir()
            (base / 'criteria').mkdir()
            oracle = b'raise AssertionError("must never execute on host")\n'
            (base / 'criteria/F01.py').write_bytes(oracle)
            (base / 'cases/F01.json').write_text(json.dumps({'case_id': 'F01',
                'allowed_files': ['main.py'], 'oracle_sha': hashlib.sha256(oracle).hexdigest()}))
            for functional in (False, True):
                with self.subTest(functional=functional), \
                     patch.object(sandbox, 'require_linux_user'), \
                     patch.object(sandbox, 'uid_thread_baseline', return_value=40), \
                     patch.object(sandbox, 'export_candidate', return_value={'main.py': 'a'*64}) as export, \
                     patch.object(sandbox, 'bwrap_argv', return_value=['synthetic-bwrap']), \
                     patch.object(sandbox, 'run_bounded', return_value=sandbox.Result(
                         0, b'{"case_id":"F01","verdict":"PASS"}', b'')):
                    result = sandbox.grade_candidate('/not-accessed', 'F01', confirmed_stopped=True,
                                                     base=base, functional_source_only=functional)
                    self.assertEqual(export.call_args.kwargs['functional_source_only'], functional)
                    self.assertEqual(result['receipt']['export_policy'],
                                     'functional-source-v1' if functional else 'strict-v1')

    def test_export_stat_ignores_atime_but_checks_every_mutation_identity_field(self):
        fields = dict(st_mtime_ns=1, st_ctime_ns=2, st_ino=3, st_dev=4,
                      st_nlink=1, st_mode=0o100600, st_size=7, st_atime_ns=8)
        original = sandbox._export_identity(SimpleNamespace(**fields))
        self.assertEqual(original, sandbox._export_identity(SimpleNamespace(**dict(fields, st_atime_ns=9))))
        for field in fields.keys() - {'st_atime_ns'}:
            with self.subTest(field=field):
                changed = dict(fields, **{field: fields[field] + 1})
                self.assertNotEqual(original, sandbox._export_identity(SimpleNamespace(**changed)))
    def test_private_namespace_and_mount_allowlist(self):
        with patch.object(Path, 'is_file', return_value=True):
            argv = sandbox.bwrap_argv('/tmp/export')
        for flag in ('--unshare-all', '--die-with-parent', '--new-session', '--clearenv'):
            self.assertIn(flag, argv)
        self.assertNotIn('--share-net', argv)
        mounts = [argv[i + 1:i + 3] for i, flag in enumerate(argv)
                  if flag in ('--bind', '--ro-bind')]
        self.assertEqual(mounts, [['/usr', '/usr'], ['/etc/ld.so.cache', '/etc/ld.so.cache'],
                                 ['/etc/localtime', '/etc/localtime'], ['/tmp/export', '/workspace']])
        self.assertNotIn('/grader', argv)
        self.assertNotIn('/mnt', argv)

    def test_outer_has_sized_workspace_and_only_explicit_private_payload(self):
        argv = sandbox.bwrap_argv('/workspace', grading=('/tmp/seed', '/tmp/trusted'))
        self.assertIn('/grader', argv)
        self.assertIn('/seed', argv)
        index = argv.index('/workspace')
        self.assertEqual(argv[index - 3:index], ['--size', str(sandbox.Limits().workspace_bytes), '--tmpfs'])

    def test_prlimit_bounded_resources(self):
        argv = sandbox.limited_argv(['bwrap'], sandbox.Limits())
        self.assertEqual(argv[0], '/usr/bin/prlimit')
        for prefix in ('--as=', '--cpu=', '--nproc=', '--fsize=', '--nofile=', '--core='):
            self.assertTrue(any(arg.startswith(prefix) for arg in argv))

    def test_bad_paths_and_limits_fail_closed(self):
        for name in ('../secret', '/etc/passwd', 'a//b', 'a/./b', 'a\\b', '', '.', 'a\x00b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                sandbox._path(name)
        with self.assertRaises(ValueError):
            sandbox.Limits(output_bytes=65537)
        with self.assertRaises(ValueError):
            sandbox.Limits(seconds=0)

    def test_grading_requires_confirmed_stop_and_registered_case(self):
        with patch.object(sandbox, 'require_linux_user'):
            with self.assertRaisesRegex(ValueError, 'confirmed stop'):
                sandbox.grade_candidate('/missing', 'F01')
            with self.assertRaisesRegex(ValueError, 'unregistered'):
                sandbox.grade_candidate('/missing', 'F99', confirmed_stopped=True)

    def test_oracle_sha_checked_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'cases').mkdir()
            (base / 'criteria').mkdir()
            (base / 'cases/F01.json').write_text(json.dumps({'case_id': 'F01', 'oracle_sha': 'bad'}))
            (base / 'criteria/F01.py').write_text('raise AssertionError("must never execute")')
            with patch.object(sandbox, 'require_linux_user'), self.assertRaisesRegex(ValueError, 'SHA mismatch'):
                sandbox.grade_candidate('/missing', 'F01', confirmed_stopped=True, base=base)

    def test_uid_thread_baseline_counts_threads_and_real_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for pid, real_uid, effective_uid, threads in [(1, 1001, 1001, 23), (2, 1001, 1001, 17),
                                                          (3, 1002, 1001, 100)]:
                entry = proc / str(pid)
                entry.mkdir()
                (entry / 'status').write_text(f'Uid:\t{real_uid}\t{effective_uid}\t1001\t1001\nThreads:\t{threads}\n')
            (proc / '4').mkdir()  # process vanished during enumeration
            self.assertEqual(sandbox.uid_thread_baseline(proc, uid=1001), 40)


@unittest.skipUnless(LINUX, 'Linux dirfd and bubblewrap integration')
class LinuxTests(unittest.TestCase):
    def setUp(self):
        sandbox.require_linux_user()
        self.temporary = tempfile.TemporaryDirectory(prefix='local-model-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.destination = self.root / 'export'
        self.source.mkdir()
        self.destination.mkdir()

    def execute(self, code, limits=None):
        limits = limits or sandbox.Limits(seconds=3)
        limits = dataclasses.replace(limits, processes=sandbox.uid_thread_baseline() + limits.processes)
        self.last_limits = limits
        argv = sandbox.bwrap_argv(self.source, limits) + ['/usr/bin/python3', '-I', '-B', '-c', code]
        return sandbox.run_bounded(sandbox.limited_argv(argv, limits), limits)

    def test_export_bytes_only_and_extras_rejected(self):
        (self.source / 'main.py').write_bytes(b'# inert bytes\n')
        expected = sandbox.export_candidate(self.source, self.destination, ['main.py'])
        self.assertEqual((self.destination / 'main.py').read_bytes(), b'# inert bytes\n')
        self.assertEqual(set(expected), {'main.py'})
        extra = self.root / 'second'
        extra.mkdir()
        (self.source / 'sitecustomize.py').write_bytes(b'raise Exception()')
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            sandbox.export_candidate(self.source, extra, ['main.py'])

    def test_functional_export_reads_only_registered_sources_and_optional_brief(self):
        (self.source / 'pkg').mkdir()
        source = self.source / 'pkg/main.py'
        source.write_bytes(b'# exact source bytes\n')
        (self.source / 'BRIEF.md').write_bytes(b'public brief\n')
        (self.source / '__pycache__').mkdir()
        (self.source / '__pycache__/ignored.pyc').write_bytes(b'cache')
        (self.source / 'sitecustomize.py').write_bytes(b'raise AssertionError("not copied")')
        (self.source / 'ignored-link').symlink_to('/not-readable')
        os.mkfifo(self.source / 'ignored-fifo')
        original_listdir = os.listdir
        def trusted_destination_only(path):
            if isinstance(path, int):
                raise AssertionError('functional export must not list source dirfds')
            return original_listdir(path)
        with patch.object(sandbox.os, 'listdir', trusted_destination_only):
            copied = sandbox.export_candidate(self.source, self.destination, ['pkg/main.py'],
                                                functional_source_only=True)
        self.assertEqual(set(copied), {'pkg/main.py', 'BRIEF.md'})
        self.assertEqual((self.destination / 'pkg/main.py').read_bytes(), source.read_bytes())
        self.assertFalse((self.destination / '__pycache__').exists())
        self.assertFalse((self.destination / 'sitecustomize.py').exists())
        self.assertTrue((self.source / '__pycache__/ignored.pyc').exists())

    def test_functional_export_required_sources_still_reject_links_and_missing(self):
        source = self.source / 'main.py'
        source.symlink_to('/not-readable')
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['main.py'], functional_source_only=True)
        source.unlink()
        other = self.root / 'other'
        other.write_bytes(b'not trusted')
        os.link(other, source)
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['main.py'], functional_source_only=True)
        source.unlink()
        os.mkfifo(source)
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['main.py'], functional_source_only=True)
        source.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            sandbox.export_candidate(self.source, self.destination, ['main.py'], functional_source_only=True)

    def test_functional_export_refuses_parent_links_and_preserves_size_caps(self):
        actual = self.root / 'outside'
        actual.mkdir()
        (actual / 'main.py').write_bytes(b'outside')
        (self.source / 'pkg').symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['pkg/main.py'], functional_source_only=True)
        (self.source / 'main.py').write_bytes(b'x'*(sandbox.MAX_FILE+1))
        with self.assertRaisesRegex(ValueError, 'oversized'):
            sandbox.export_candidate(self.source, self.destination, ['main.py'], functional_source_only=True)
        (self.source / 'main.py').write_bytes(b'123456')
        (self.source / 'other.py').write_bytes(b'abcdef')
        with patch.object(sandbox, 'MAX_EXPORT', 10), self.assertRaisesRegex(ValueError, 'size limit'):
            sandbox.export_candidate(self.source, self.destination, ['main.py', 'other.py'], functional_source_only=True)

    def test_export_succeeds_when_its_read_updates_stale_access_time(self):
        candidate = self.source / 'main.py'
        candidate.write_bytes(b'# inert fresh bytes\n')
        original = candidate.stat()
        os.utime(candidate, ns=(1, original.st_mtime_ns))
        copied = sandbox.export_candidate(self.source, self.destination, ['main.py'])
        self.assertEqual(copied['main.py'], hashlib.sha256(b'# inert fresh bytes\n').hexdigest())
        self.assertEqual((self.destination / 'main.py').read_bytes(), b'# inert fresh bytes\n')

    def test_export_links_special_files_oversize_missing(self):
        candidate = self.source / 'main.py'
        candidate.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['main.py'])
        candidate.unlink()
        target = self.root / 'target'
        target.write_bytes(b'private')
        os.link(target, candidate)
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['main.py'])
        candidate.unlink()
        os.mkfifo(candidate)
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['main.py'])
        candidate.unlink()
        candidate.write_bytes(b'x' * (sandbox.MAX_FILE + 1))
        with self.assertRaises(ValueError):
            sandbox.export_candidate(self.source, self.destination, ['main.py'])
        candidate.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            sandbox.export_candidate(self.source, self.destination, ['main.py'])

    def test_export_symlink_ancestor_refused(self):
        (self.source / 'main.py').write_bytes(b'pass')
        linked = self.root / 'link'
        linked.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(OSError):
            sandbox.export_candidate(linked, self.destination, ['main.py'])

    def test_filesystem_network_process_and_memory_limits(self):
        sentinel = self.root / 'sentinel'
        sentinel.write_bytes(b'private')
        code = f'''import json, os, resource, socket
from pathlib import Path
Path("ok").write_text("allowed")
hidden = ["/mnt/c", "/mnt/d", "/grader", "/opt/model-runtime", {str(sentinel)!r}, "/run/docker.sock"]
s = socket.socket()
try:
    s.connect(("1.1.1.1", 53)); network = True
except OSError:
    network = False
print(json.dumps({{"hidden": [Path(p).exists() for p in hidden], "network": network,
                  "nproc": resource.getrlimit(resource.RLIMIT_NPROC)[0],
                  "memory": resource.getrlimit(resource.RLIMIT_AS)[0]}}))
'''
        result = self.execute(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['hidden'], [False] * 6)
        self.assertFalse(value['network'])
        self.assertEqual(value['nproc'], self.last_limits.processes)
        self.assertEqual(value['memory'], sandbox.Limits().memory_bytes)
        self.assertEqual((self.source / 'ok').read_text(), 'allowed')

    def test_no_other_writable_mounts_or_new_user_namespace(self):
        result = self.execute('''import os, subprocess
from pathlib import Path
for path in ("/root-write", "/dev/extra"):
    try:
        Path(path).write_bytes(b"bypass")
        raise AssertionError("writable unsized tmpfs")
    except OSError:
        pass
p = subprocess.run(["/usr/bin/unshare", "--user", "--map-root-user", "true"], capture_output=True)
assert p.returncode != 0, "candidate can regain mount capabilities"
print("bounded")
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'bounded\n')

    def test_workspace_tmpfs_quota_enforced(self):
        # This probe executes inside the complete grader sandbox so the mounted
        # workspace actually is the bounded tmpfs, rather than a test host bind.
        base = self.root / 'campaign'
        (base / 'cases').mkdir(parents=True)
        (base / 'criteria').mkdir()
        code = '''from pathlib import Path
def grade(root, runner):
    result = runner(["main.py"], cwd=root)
    passed = result.returncode == 0 and result.stdout == b"private bounded workspace\\n"
    return {"case_id": "F01", "criteria": [{"id": "boundary", "passed": passed}],
            "verdict": "PASS" if passed else "FAIL"}
'''
        (base / 'criteria/F01.py').write_text(code)
        (base / 'cases/F01.json').write_text(json.dumps({
            'case_id': 'F01', 'allowed_files': ['main.py'],
            'oracle_sha': hashlib.sha256(code.encode()).hexdigest()}))
        (self.source / 'main.py').write_text('''import os
from pathlib import Path
assert not Path("/grader").exists()
assert not Path("/seed").exists()
capacity = os.statvfs(".").f_blocks * os.statvfs(".").f_frsize
assert capacity == 64 * 1024 * 1024, capacity
try:
    for i in range(16):
        Path("fill-" + str(i)).write_bytes(b"x" * (8 * 1024 * 1024))
except OSError:
    print("private bounded workspace")
else:
    raise AssertionError("workspace quota not enforced")
''')
        result = sandbox.grade_candidate(self.source, 'F01', confirmed_stopped=True, base=base)
        self.assertEqual(result['status'], 'GRADED', result)
        self.assertEqual(result['result']['verdict'], 'PASS', result)

    def test_timeout_output_and_detached_descendants(self):
        timeout = self.execute('while True: pass', sandbox.Limits(seconds=0.25))
        self.assertTrue(timeout.timed_out)
        self.assertEqual(timeout.returncode, 124)
        output = self.execute('import os\nwhile True: os.write(1,b"x"*8192)')
        self.assertTrue(output.output_limited)
        self.assertEqual(len(output.stdout), 65536)
        self.assertEqual(output.returncode, 125)
        detached = self.execute('''import os, time
if os.fork() == 0:
    os.setsid()
    time.sleep(.4)
    open("orphan-write", "w").write("unsafe")
else:
    print("done", flush=True)
''')
        self.assertEqual(detached.returncode, 0, detached.stderr)
        time.sleep(.5)
        self.assertFalse((self.source / 'orphan-write').exists())

    def test_nested_private_grader_and_unsolved_seed(self):
        # Operator-authored inert failing program: candidate bytes are executed
        # exclusively in the nested sandbox. Whole grader also runs in bwrap.
        (self.source / 'main.py').write_text('raise SystemExit(2)\n')
        result = sandbox.grade_candidate(self.source, 'F01', confirmed_stopped=True)
        self.assertEqual(result['status'], 'GRADED', result)
        self.assertEqual(result['result']['verdict'], 'FAIL')
        self.assertFalse(any(p.name.startswith('.grader') for p in self.source.iterdir()))

    def test_grader_admits_existing_real_uid_baseline_above_32(self):
        stop = threading.Event()
        threads = [threading.Thread(target=stop.wait) for _ in range(36)]
        for thread in threads:
            thread.start()
        try:
            (self.source / 'main.py').write_text('raise SystemExit(2)\n')
            result = sandbox.grade_candidate(self.source, 'F01', confirmed_stopped=True)
            self.assertEqual(result['status'], 'GRADED', result)
            receipt = result['receipt']
            self.assertGreater(receipt['uid_thread_baseline'], 32)
            self.assertEqual(receipt['uid_thread_limit'], receipt['uid_thread_baseline'] + 32)
            self.assertEqual(receipt['candidate_limits']['processes'], receipt['uid_thread_limit'])
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=2)

    def test_queue_python_module_sqlite_and_unittest_contract(self):
        # Existing operator-authored prepared F06 baseline solves F05; copying
        # bytes here does not import or execute the candidate on the host.
        manifest = json.loads((sandbox.BASE / 'cases/F05.json').read_text())
        for name in manifest['allowed_files']:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((sandbox.BASE / 'seeds/F06' / name).read_bytes())
        result = sandbox.grade_candidate(self.source, 'F05', confirmed_stopped=True)
        self.assertEqual(result['status'], 'GRADED', result)
        self.assertEqual(result['result']['verdict'], 'PASS', result)


if __name__ == '__main__':
    unittest.main()
