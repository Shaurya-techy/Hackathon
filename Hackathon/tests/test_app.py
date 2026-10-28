"""Basic integration tests. Install requirements.txt, then run: python -m unittest discover -s tests"""
import os
import re
import tempfile
import unittest

from app import create_app


class ComplaintDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "DATABASE": os.path.join(self.temp_dir.name, "test.db"),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self, response):
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, "CSRF token was not rendered in the page")
        return match.group(1).decode()

    def login(self, username="admin", password="Admin@123"):
        response = self.client.get("/login")
        token = self.csrf_token(response)
        return self.client.post("/login", data={
            "csrf_token": token, "username": username, "password": password,
        }, follow_redirects=True)

    def test_login_and_dashboard(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Complaint overview", response.data)

    def test_complaint_register_and_export(self):
        self.login()
        response = self.client.get("/complaints/new")
        token = self.csrf_token(response)
        created = self.client.post("/complaints/new", data={
            "csrf_token": token,
            "institution_name": "Test Engineering College",
            "subject": "Test complaint",
            "description": "Test description for the unit test.",
            "category": "Other",
            "priority": "Medium",
            "department_id": "",
            "due_date": "",
        }, follow_redirects=True)
        self.assertEqual(created.status_code, 200)
        self.assertIn(b"Test complaint", created.data)
        exported = self.client.get("/export/complaints.csv")
        self.assertEqual(exported.status_code, 200)
        self.assertIn(b"complaint_number", exported.data)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

    def test_post_without_csrf_is_rejected(self):
        response = self.client.post("/login", data={"username": "admin", "password": "Admin@123"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
