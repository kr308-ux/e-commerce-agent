import os
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from creator_contact.services.subprocess_control import (
    TaskCancellationRequested,
    run_task_subprocess,
)
from creator_contact.services.worker_runtime import (
    WorkerHealthServer,
    worker_endpoint_ready,
)


class WorkerRuntimeTests(SimpleTestCase):
    def test_health_endpoint_identifies_expected_worker(self):
        server = WorkerHealthServer("127.0.0.1", 0)
        port = int(server.server.server_address[1])
        server.start()
        try:
            self.assertTrue(worker_endpoint_ready("127.0.0.1", port))
        finally:
            server.close()
        self.assertFalse(worker_endpoint_ready("127.0.0.1", port))

    def test_cancelled_task_terminates_active_process_tree(self):
        process = Mock()
        process.pid = 12345
        process.poll.return_value = None
        process.communicate.return_value = ("", "")

        with patch(
            "creator_contact.services.subprocess_control."
            "task_cancellation_requested",
            return_value=True,
        ), patch(
            "creator_contact.services.subprocess_control."
            "_terminate_process_tree",
        ) as terminate, patch(
            "creator_contact.services.subprocess_control."
            "_record_active_process",
            return_value=True,
        ), patch(
            "creator_contact.services.subprocess_control.subprocess.Popen",
            return_value=process,
        ) as popen:
            with self.assertRaises(TaskCancellationRequested):
                run_task_subprocess(
                    "task-id",
                    ["worker", "--run"],
                    cwd="/tmp",
                    env={},
                    timeout=30,
                )

        terminate.assert_called_once_with(process)
        popen_kwargs = popen.call_args.kwargs
        if os.name == "posix":
            self.assertTrue(popen_kwargs["start_new_session"])
