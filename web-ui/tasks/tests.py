from django.test import SimpleTestCase
from django.urls import reverse


class UiPageTests(SimpleTestCase):
    def test_dashboard_renders(self) -> None:
        response = self.client.get(reverse("tasks:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "让浏览器替你完成重复工作")

    def test_task_detail_renders(self) -> None:
        response = self.client.get(reverse("tasks:task-detail"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "任务执行详情")
