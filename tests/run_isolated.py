"""Repository regression entry point: subprocesses and outbound sockets prohibited."""
import contextlib
import pathlib
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
attempts=[]
def deny(*args,**kwargs):
    attempts.append('blocked external operation')
    raise AssertionError('Real device, subprocess or outbound connection forbidden')
with contextlib.ExitStack() as stack:
    for name in ('run','Popen'):
        stack.enter_context(patch.object(subprocess,name,side_effect=deny))
    for name in ('connect','connect_ex'):
        stack.enter_context(patch.object(socket.socket,name,side_effect=deny))
    import mirror_server as m
    sys.path.insert(0, str(ROOT / "tests"))
    from local_browser_client import LocalBrowserClient
    m.app.test_client_class = LocalBrowserClient
    folder=pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
    for name in ('QA_RESULTS_DIR','RECORDINGS_DIR'):
        stack.enter_context(patch.object(m,name,folder))
    for name in ('AI_HISTORY_FILE','AI_TEST_HISTORY_FILE','TEST_RUN_HISTORY_FILE','REDMINE_FAILURES_FILE'):
        stack.enter_context(patch.object(m,name,folder/(name+'.jsonl')))
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print('External operation attempts:',len(attempts))
sys.exit(0 if result.wasSuccessful() and not attempts else 1)

