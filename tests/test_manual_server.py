import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import run


class ManualServerTests(unittest.TestCase):
    def exercise(self, demo, fail_funnel=False):
        # Exercise the launcher without publishing an endpoint or starting a real DB or GPU.
        worker = MagicMock()
        worker.poll.return_value = None
        funnel = MagicMock()
        funnel.poll.return_value = 1 if fail_funnel else None
        funnel.returncode = 1 if fail_funnel else None
        launched = threading.Event()
        with ExitStack() as stack:
            folder = stack.enter_context(tempfile.TemporaryDirectory(dir=run.ROOT))
            state = Path(folder)
            (state / "logs").mkdir()
            stack.enter_context(patch.object(run, "STATE", state))
            stack.enter_context(patch.object(run, "environment"))
            stack.enter_context(patch.object(run, "database_start", return_value=True))
            pg_ctl = stack.enter_context(patch.object(run, "pg_ctl"))
            stack.enter_context(patch("driver.db.initialize"))
            stack.enter_context(patch("driver.db.connect"))
            stack.enter_context(patch.dict(run.os.environ))
            which = stack.enter_context(patch.object(run.shutil, "which", return_value="tailscale"))
            stack.enter_context(patch.object(sys, "argv", ["run.py", "--port", "0"] + (["--demo"] if demo else [])))
            server = stack.enter_context(patch("uvicorn.Server")).return_value
            server.started = server.should_exit = False

            def spawn(command, **kwargs):
                if "--worker" in command:
                    return worker
                self.assertTrue(server.started)
                self.assertEqual(command, ["tailscale", "funnel", "--https=10000", "http://127.0.0.1:0"])
                launched.set()
                return funnel

            stack.enter_context(patch.object(run.subprocess, "Popen", side_effect=spawn))

            def serve():
                server.started = True
                if demo:
                    self.assertTrue(launched.wait(timeout=5))
                if not fail_funnel:
                    (state / "stop.request").touch()
                deadline = time.monotonic() + 5
                while not server.should_exit and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(server.should_exit)

            server.run.side_effect = serve
            if fail_funnel:
                with self.assertRaisesRegex(SystemExit, "Funnel exited"):
                    run.main()
            else:
                run.main()
            self.assertEqual(launched.is_set(), demo)
            self.assertEqual(which.called, demo)
            self.assertEqual(funnel.terminate.called, demo and not fail_funnel)
            worker.stdin.close.assert_called_once()
            pg_ctl.assert_called_once_with("stop", "-w", "-m", "fast")
            self.assertFalse((state / "server-url.txt").exists())

    def test_manual_local_and_demo_lifetimes(self):
        for demo, failure in ((False, False), (True, False), (True, True)):
            with self.subTest(demo=demo, failure=failure):
                self.exercise(demo, failure)
