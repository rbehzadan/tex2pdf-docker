from locust import HttpUser, task, between
import os
import random
import time
from pathlib import Path

class Tex2PDFUser(HttpUser):
    wait_time = between(1, 2)

    def on_start(self):
        self.test_files = list(Path("test_projects").glob("*.zip"))
        api_key = os.getenv("LOCUST_API_KEY")
        self.headers = {"X-API-Key": api_key} if api_key else {}

    @task
    def full_conversion_flow(self):
        selected_file = random.choice(self.test_files)
        with open(selected_file, "rb") as f:
            files = {
                "zip_file": (selected_file.name, f, "application/zip")
            }
            with self.client.post("/tex2pdf", files=files, headers=self.headers, catch_response=True) as response:
                if response.status_code != 200:
                    response.failure(f"{selected_file.name}: upload failed {response.status_code}")
                    return
                try:
                    job_id = response.json().get("job_id")
                    if not job_id:
                        response.failure(f"{selected_file.name}: No job_id returned")
                        return
                except Exception:
                    response.failure(f"{selected_file.name}: Invalid JSON response")
                    return

        # Poll for completion
        for _ in range(30):  # wait up to ~30s
            with self.client.get(f"/tex2pdf/status/{job_id}", headers=self.headers, name="/tex2pdf/status") as status_resp:
                if status_resp.status_code != 200:
                    status_resp.failure(f"{selected_file.name}: Status check failed")
                    return

                status = status_resp.json().get("status")
                if status == "completed":
                    break
                elif status == "failed":
                    status_resp.failure(f"{selected_file.name}: Job failed")
                    return
                time.sleep(1)
        else:
            response.failure(f"{selected_file.name}: Job timed out")
            return

        # Download the PDF
        with self.client.get(f"/tex2pdf/download/{job_id}", headers=self.headers, name="/tex2pdf/download") as download_resp:
            if download_resp.status_code != 200:
                download_resp.failure(f"{selected_file.name}: PDF download failed")
                return
            content = download_resp.content
            if not content.startswith(b"%PDF"):
                download_resp.failure(f"{selected_file.name}: Not a valid PDF")
                return

