"""Select monitoring candidates by workflow state and collection time."""

from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from data.job_store import JobStore


class MonitorSelectionTests(unittest.TestCase):
    def test_only_greeted_jobs_after_cutoff(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        store = JobStore(conn, score_threshold=71)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        for job_id in ("greeted", "scored", "old"):
            store.save({"source_platform": "boss", "source_job_id": job_id,
                        "ai_score_status": "scored", "ai_score": 70 if job_id == "scored" else 85})
        for job_id in ("greeted", "old"):
            store.set_status("boss", job_id, "greeted")
        conn.execute("UPDATE collected_jobs SET collected_at = ? WHERE source_job_id = 'old'",
                     ((cutoff - timedelta(seconds=1)).isoformat(),))
        conn.commit()

        self.assertEqual({job["source_job_id"] for job in store.list_monitorable_jobs(cutoff)},
                         {"greeted"})
        with self.assertRaisesRegex(ValueError, "时区"):
            store.list_monitorable_jobs(datetime.now())


if __name__ == "__main__":
    unittest.main()
