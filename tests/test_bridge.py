"""Bridge delegation tests (no AEDT, no MCP server)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from antopt.bridge import InProcessBridge, StdioBridge, make_bridge

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class FakeMcp:
    def __init__(self):
        self.calls = []

    def clear_stale_lock(self, project_path):
        self.calls.append(("clear_stale_lock", project_path))
        return {"cleared": True, "owner_pid": 1}

    def run_python_code(self, code, pid=None, port=None):
        self.calls.append(("run_python_code", code, pid, port))
        return {"executed": True, "result": {"ok": True}}

    def connect_to_aedt(self, pid=None, port=None, non_graphical=False):
        self.calls.append(("connect_to_aedt", pid, port, non_graphical))
        return {"connected": True}

    def resume_guarded_run(self, campaign_dir, run_id, pid=None, port=None):
        self.calls.append(("resume", campaign_dir, run_id, pid))
        return {"resumed": True}

    def submit_guarded_run(self, **kwargs):
        self.calls.append(("submit", kwargs))
        return {"run_id": "r1"}

    def get_run_status(self, campaign_dir, run_id):
        self.calls.append(("status", campaign_dir, run_id))
        return {"status": "COMPLETED"}

    def archive_query(self, campaign_dir):
        self.calls.append(("archive", campaign_dir))
        return {"best": None}


class InProcessBridgeTests(unittest.TestCase):
    def _bridge(self):
        bridge = InProcessBridge.__new__(InProcessBridge)
        bridge._mcp = FakeMcp()
        return bridge

    def test_delegates_guard_calls(self):
        bridge = self._bridge()
        self.assertTrue(bridge.clear_stale_lock("p.aedt")["cleared"])
        bridge.execute_code("x = 1", pid=7)
        bridge.connect(pid=7)
        bridge.submit(campaign_dir="c", project_name="p")
        bridge.status("c", "r1")
        bridge.archive("c")
        bridge.resume("c", "r1", pid=3)
        names = [call[0] for call in bridge._mcp.calls]
        self.assertEqual(names, ["clear_stale_lock", "run_python_code",
                                 "connect_to_aedt", "submit", "status", "archive",
                                 "resume"])

    def test_execute_code_passes_target(self):
        bridge = self._bridge()
        bridge.execute_code("x = 1", pid=9, port=None)
        call = bridge._mcp.calls[0]
        self.assertEqual(call[2], 9)


class StdioBridgeTests(unittest.TestCase):
    def _bridge(self):
        bridge = StdioBridge.__new__(StdioBridge)
        bridge.calls = []

        def fake_call(name, arguments):
            bridge.calls.append((name, arguments))
            return {"ok": name}

        bridge._call = fake_call  # type: ignore[method-assign]
        return bridge

    def test_tool_names_and_arguments(self):
        bridge = self._bridge()
        bridge.clear_stale_lock("p.aedt")
        bridge.execute_code("x = 1", pid=5)
        bridge.connect(port=1234)
        bridge.submit(campaign_dir="c")
        bridge.status("c", "r1")
        bridge.archive("c")
        bridge.resume("c", "r1")
        names = [name for name, _ in bridge.calls]
        self.assertEqual(names, ["clear_stale_lock", "run_python_code",
                                 "connect_to_aedt", "submit_guarded_run",
                                 "get_run_status", "archive_query", "resume_guarded_run"])
        self.assertEqual(bridge.calls[0][1], {"project_path": "p.aedt"})
        self.assertEqual(bridge.calls[2][1]["port"], 1234)

    def test_unknown_bridge_kind(self):
        with self.assertRaises(ValueError):
            make_bridge("carrier-pigeon")


if __name__ == "__main__":
    unittest.main()

