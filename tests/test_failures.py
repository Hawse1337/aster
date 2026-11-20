"""Failure classification: every code the system produces must explain itself."""
from __future__ import annotations

import unittest

from aster.failures import STALLED, classify


class ClassificationTests(unittest.TestCase):
    def test_the_codes_this_goal_hit_are_all_covered(self):
        cases = {
            "E_NOT_CONVERGED": "求解没有收敛",
            "E_SWEEP_EXPORT_INCOMPLETE": "扫频导出不完整",
            "E_FF_SPHERE_MISSING": "远场球设置缺失",
            "INVALID_PARAMS": "参数超出范围",
            "GEOMETRY_REJECTED": "几何无法生成",
            "NO_AEDT": "找不到可用的 AEDT",
            "REAL_DISABLED": "当前是只读模式",
            STALLED: "求解停滞",
        }
        for code, title in cases.items():
            with self.subTest(code=code):
                failure = classify(code)
                self.assertEqual(failure.title, title)
                self.assertTrue(failure.action)

    def test_a_message_without_a_code_still_classifies(self):
        failure = classify(None, "WorkerRemoteError: Failed to start new AEDT gRPC "
                                 "session on port 0")
        self.assertEqual(failure.title, "AEDT 会话断开了")
        self.assertTrue(failure.retryable)

    def test_insert_frequency_sweep_is_recognised_from_the_message(self):
        failure = classify("RUN_FAILED",
                           "backend_error: Failed to execute gRPC AEDT command: "
                           "InsertFrequencySweep")
        self.assertEqual(failure.title, "扫频设置没写进去")
        self.assertTrue(failure.retryable)

    def test_create_report_is_not_retryable(self):
        failure = classify(None, "GrpcApiError: Failed to execute gRPC AEDT command: CreateReport")
        self.assertFalse(failure.retryable)

    def test_a_stall_is_retryable(self):
        self.assertTrue(classify("STALLED").retryable)

    def test_unknown_codes_say_so_instead_of_guessing(self):
        failure = classify("SOMETHING_NEW", "boom")
        self.assertEqual(failure.title, "未分类的失败")
        self.assertEqual(failure.detail, "boom")
        self.assertFalse(failure.retryable)

    def test_every_failure_serialises_for_the_page(self):
        payload = classify("E_NOT_CONVERGED").to_dict()
        self.assertEqual(set(payload), {"code", "title", "detail", "action", "retryable"})
        self.assertIsInstance(payload["retryable"], bool)


if __name__ == "__main__":
    unittest.main()
