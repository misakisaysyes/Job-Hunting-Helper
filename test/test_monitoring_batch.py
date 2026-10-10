"""Persisted monitoring batches never count uncertain external actions as completed."""

from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from api.followups import FollowupSkipped
from api.jobs import JobActionError
from api.monitoring_batch import MonitoringBatchRun
from api.server import make_handler
from data.task_run_store import LOCAL_ZONE, TaskAlreadyRunning, TaskRunStore


def followup_item(conversation_id):
    return {"platform": "boss", "conversation_id": conversation_id,
            "text": "您好，希望投递简历。", "anchor_id": "100", "expected_text": "原招呼语"}


class MonitoringBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.runner = MonitoringBatchRun(self.path)

    def queued(self, action, items):
        with patch("api.monitoring_batch.Thread") as worker:
            state = self.runner.start(action, items)
            args = worker.call_args.kwargs["args"]
        return state, args

    def test_progress_survives_a_new_service_instance_before_batch_finishes(self):
        first_done, release, finished = Event(), Event(), Event()

        def send(*args, **kwargs):
            if args[3] == "2":
                first_done.set()
                if not release.wait(3):
                    raise RuntimeError("测试等待超时")

        original_finish = self.runner.run_store.finish

        def finish(*args, **kwargs):
            try:
                original_finish(*args, **kwargs)
            finally:
                finished.set()

        with patch("api.monitoring_batch.send_followup", side_effect=send), \
                patch.object(self.runner.run_store, "finish", side_effect=finish):
            state = self.runner.start("followup", [followup_item("1"), followup_item("2")])
            try:
                self.assertEqual((state["status"], state["counts"]["completed"]), ("running", 0))
                self.assertTrue(first_done.wait(3))
                restored = MonitoringBatchRun(self.path).snapshots()["followup"]
                self.assertEqual(restored["run_id"], state["run_id"])
                self.assertEqual(restored["status"], "running")
                self.assertEqual(restored["counts"],
                    {"total": 2, "completed": 1, "processed": 1, "failed": 0, "skipped": 0})
            finally:
                release.set()
                self.assertTrue(finished.wait(3))
        restored = MonitoringBatchRun(self.path).get(state["run_id"])
        self.assertEqual(restored["status"], "completed")
        self.assertEqual(restored["counts"]["completed"], 2)

    def test_uncertain_followup_stops_the_remaining_batch_and_records_verified_count(self):
        state, args = self.queued("followup", [followup_item(str(i)) for i in (1, 2, 3)])
        with patch("api.monitoring_batch.send_followup",
                   side_effect=[{}, JobActionError("发送结果不明", 409)]) as send:
            self.runner._execute(*args)
        result = self.runner.get(state["run_id"])
        self.assertEqual(result["status"], "completed_with_shortage")
        self.assertEqual(result["counts"],
            {"total": 3, "completed": 1, "processed": 2, "failed": 1, "skipped": 1})
        self.assertEqual([call.args[3] for call in send.call_args_list], ["1", "2"])
        self.assertEqual(send.call_args_list[0].kwargs["expected_text"], "原招呼语")
        self.assertIn("会话 2：发送结果不明", result["message"])

    def test_missing_target_is_skipped_without_stopping_the_remaining_followups(self):
        state, args = self.queued("followup", [followup_item(str(i)) for i in (1, 2, 3)])
        with patch("api.monitoring_batch.send_followup", side_effect=[
                {}, FollowupSkipped("「仅沟通」中未找到目标会话，已跳过，未发送"), {}]) as send:
            self.runner._execute(*args)
        result = MonitoringBatchRun(self.path).get(state["run_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counts"],
            {"total": 3, "completed": 2, "processed": 3, "failed": 0, "skipped": 1})
        self.assertEqual([call.args[3] for call in send.call_args_list], ["1", "2", "3"])
        self.assertIn("成功 2，跳过 1，失败 0", result["message"])
        self.assertIn("跳过会话 2：「仅沟通」中未找到目标会话", result["message"])

    def test_all_missing_targets_complete_with_each_skip_reason_in_history(self):
        state, args = self.queued("followup", [followup_item(str(i)) for i in (1, 2)])
        with patch("api.monitoring_batch.send_followup",
                   side_effect=FollowupSkipped("「仅沟通」中未找到目标会话")) as send:
            self.runner._execute(*args)
        result = self.runner.get(state["run_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counts"],
            {"total": 2, "completed": 0, "processed": 2, "failed": 0, "skipped": 2})
        self.assertEqual(send.call_count, 2)
        day = datetime.fromisoformat(state["started_at"]).astimezone(LOCAL_ZONE).date()
        message = self.runner.run_store.for_date(day)["monitoring_followup"]["history"][0]["message"]
        self.assertIn("跳过会话 1：", message)
        self.assertIn("跳过会话 2：", message)

    def test_skip_then_uncertain_send_keeps_skipped_count_and_stops_unsent_items(self):
        state, args = self.queued("followup", [followup_item(str(i)) for i in (1, 2, 3)])
        with patch("api.monitoring_batch.send_followup", side_effect=[
                FollowupSkipped("「仅沟通」中未找到目标会话"), JobActionError("发送结果不明", 409)]) as send:
            self.runner._execute(*args)
        result = self.runner.get(state["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["counts"],
            {"total": 3, "completed": 0, "processed": 2, "failed": 1, "skipped": 2})
        self.assertEqual([call.args[3] for call in send.call_args_list], ["1", "2"])
        self.assertIn("跳过会话 1：", result["message"])
        self.assertIn("会话 2：发送结果不明", result["message"])

    def test_failed_first_delete_does_not_count_planned_deletions_as_success(self):
        items = [{"platform": "boss", "conversation_id": str(i)} for i in (1, 2)]
        state, args = self.queued("delete", items)
        with patch("api.monitoring_batch.delete_candidate", side_effect=JobActionError("目标已变化", 409)) as delete:
            self.runner._execute(*args)
        result = self.runner.get(state["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["counts"],
            {"total": 2, "completed": 0, "processed": 1, "failed": 1, "skipped": 1})
        delete.assert_called_once()

    def test_confirmed_deletions_use_the_start_config_and_preserve_retry_confirmation(self):
        state, args = self.queued("delete", [
            {"platform": "boss", "conversation_id": "1"},
            {"platform": "boss", "conversation_id": "2", "confirm_still_present": True},
        ])
        config = args[3]
        config["profile"]["blocked_companies"] = ["测试排除词"]
        with patch("api.monitoring_batch.delete_candidate", return_value={}) as delete:
            self.runner._execute(*args)
        self.assertEqual([call.kwargs["confirm_still_present"] for call in delete.call_args_list], [False, True])
        self.assertTrue(all(call.args[4] == ["测试排除词"] for call in delete.call_args_list))
        self.assertEqual(self.runner.get(state["run_id"])["counts"]["completed"], 2)

    def test_interruption_retains_progress_and_prevents_late_worker_completion(self):
        state, args = self.queued("followup", [followup_item("1"), followup_item("2")])
        counts = {"total": 2, "completed": 1, "processed": 1, "failed": 0, "skipped": 0}
        self.runner.run_store.update_counts(state["run_id"], counts)
        self.runner.run_store.mark_interrupted()
        with patch("api.monitoring_batch.send_followup") as send:
            self.runner._execute(*args)
        send.assert_not_called()
        self.runner.run_store.finish(state["run_id"], "completed", "迟到结果", {}, "2026-10-10T00:00:00+00:00")
        restored = self.runner.get(state["run_id"])
        self.assertEqual((restored["status"], restored["counts"]), ("interrupted", counts))

    def test_collection_scan_and_batches_share_one_running_slot(self):
        for task_type in ("collection", "monitoring", "monitoring_followup", "monitoring_delete"):
            with self.subTest(task_type=task_type):
                active = self.runner.run_store.start(task_type, "2026-10-10T00:00:00+00:00", "运行中")
                with patch("api.monitoring_batch.Thread") as worker, self.assertRaises(TaskAlreadyRunning):
                    self.runner.start("delete", [{"platform": "boss", "conversation_id": "1"}])
                worker.assert_not_called()
                self.runner.run_store.finish(active, "completed", "完成", {}, "2026-10-10T00:01:00+00:00")

    def test_invalid_batch_inputs_never_start_a_task(self):
        valid = followup_item("1")
        invalid = [
            ([], [valid]), ("other", [valid]), ("followup", []),
            ("followup", [valid] * 2), ("followup", [{**valid, "text": " "}]),
            ("followup", [{**valid, "text": "字" * 301}]),
            ("followup", [{**valid, "anchor_id": ""}]),
            ("followup", [{**valid, "expected_text": None}]),
            ("delete", [{"platform": "boss", "conversation_id": "bad-id"}]),
            ("delete", [{"platform": "other", "conversation_id": "1"}]),
            ("delete", [{"platform": "boss", "conversation_id": str(i)} for i in range(101)]),
        ]
        with patch("api.monitoring_batch.Thread") as worker:
            for action, items in invalid:
                with self.subTest(action=action, items=items), self.assertRaises(JobActionError):
                    self.runner.start(action, items)
        worker.assert_not_called()
        self.assertIsNone(self.runner.run_store.active_task_type())
        self.assertIsNone(self.runner.snapshots()["followup"])

    def test_thread_launch_failure_releases_running_slot(self):
        with patch("api.monitoring_batch.Thread") as worker:
            worker.return_value.start.side_effect = RuntimeError("线程无法启动")
            with self.assertRaises(JobActionError) as error:
                self.runner.start("followup", [followup_item("1")])
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(self.runner.snapshots()["followup"]["status"], "failed")
        self.assertIsNone(self.runner.run_store.active_task_type())

    def test_daily_and_historical_buckets_keep_each_batch_type_separate(self):
        store = TaskRunStore(self.path)
        for task_type, completed in (("monitoring_followup", 2), ("monitoring_delete", 3)):
            run_id = store.start(task_type, "2026-10-09T16:01:00+00:00", "执行中",
                counts={"total": completed, "completed": 0})
            store.finish(run_id, "completed", "完成", {"total": completed, "completed": completed},
                "2026-10-10T00:02:00+00:00")
        summary = store.for_date(date(2026, 10, 10))
        self.assertEqual(summary["monitoring"]["runs"], 0)
        self.assertEqual(summary["monitoring_followup"]["totals"]["completed"], 2)
        self.assertEqual(summary["monitoring_delete"]["totals"]["completed"], 3)
        self.assertEqual(store.for_date(date(2026, 10, 9))["monitoring_delete"]["runs"], 0)

    def test_api_exposes_running_batches_after_handler_recreation(self):
        def handler():
            cls = make_handler(self.path, Mock(), restart_event=Event(), started_ai_concurrency=1,
                state_db_overridden=True, server_instance_id="test")
            instance = object.__new__(cls)
            instance.send_json = Mock()
            instance.read_json = Mock(return_value={"action": "followup", "items": [followup_item("1")]})
            return instance

        api = handler()
        api.path = "/api/monitoring/batches"
        with patch("api.monitoring_batch.Thread"):
            api.do_POST()
        code, state = api.send_json.call_args.args
        self.assertEqual((code, state["status"]), (202, "running"))
        api = handler()
        api.path = "/api/tasks/today"
        api.do_GET()
        code, summary = api.send_json.call_args.args
        self.assertEqual(code, 200)
        self.assertTrue(summary["active"]["monitoring_followup"])
        self.assertTrue(summary["active"]["any"])
        api.path = "/api/monitoring/current"
        api.do_GET()
        self.assertEqual(api.send_json.call_args.args[1]["batches"]["followup"]["run_id"], state["run_id"])
        api.path = f"/api/monitoring/batches/{state['run_id']}"
        api.do_GET()
        self.assertEqual(api.send_json.call_args.args[1]["counts"]["total"], 1)
        api.path = "/api/monitoring/batches/9999"
        api.do_GET()
        self.assertEqual(api.send_json.call_args.args[0], 404)
        api.path = "/api/monitoring"
        api.do_POST()
        self.assertEqual(api.send_json.call_args.args[0], 409)


if __name__ == "__main__":
    unittest.main()
