from django.test import SimpleTestCase

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
