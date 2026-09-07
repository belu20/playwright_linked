import datetime
import json
import logging
import os
import time
import pytz
from logging.handlers import RotatingFileHandler

class Logger:
    def __init__(self, service_name: str, vm_name: str, log_status_value,
                 max_bytes: int = 20 * 1024 * 1024, backup_count: int = 3):
        self.service_name = service_name
        self.vm_name = vm_name
        self.log_status_value = log_status_value
        self.log_dir = os.environ.get("LOG_DIR", "logs")
        os.makedirs(self.log_dir, exist_ok=True)

        # ---- Rotating file logger ----
        # Sebelumnya file log dibuka dengan open(...,"a") tanpa batas ukuran,
        # sehingga bisa bertumbuh tanpa henti dan menambah Block I/O container.
        # RotatingFileHandler otomatis memutar file setelah mencapai max_bytes,
        # dan hanya menyimpan backup_count file lama.
        log_file_path = os.path.join(self.log_dir, f"{self.vm_name}.log")
        self._file_logger = logging.getLogger(f"crawler_log_{self.vm_name}")
        self._file_logger.setLevel(logging.INFO)
        self._file_logger.propagate = False
        if not self._file_logger.handlers:
            handler = RotatingFileHandler(
                log_file_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._file_logger.addHandler(handler)

        self._logic_list = {
            0000: {"code": "Crawling Summary", "level": "INFO"},
            4401: {"code": "No Ready Cookie", "level": "Warning"},
            4402: {"code": "Missing Configuration", "level": "Warning"},
            4403: {"code": "Requested Resource Not Found", "level": "Warning"},
            4404: {"code": "Target Restricted", "level": "Warning"},
            4501: {"code": None, "level": "Error"},
            4502: {"code": "Credentials Expired", "level": "Error"},
            4503: {"code": "Failed to Store Data in Object Storage", "level": "Error"},
            4504: {"code": "Account Blocked", "level": "Error"},
            4601: {"code": "Database Connection Refused", "level": "Critical"},
            4602: {"code": "No Route to Database Host", "level": "Critical"},
            4603: {"code": "API Request Failed", "level": "Critical"},
            4604: {"code": "Connection to Queue Failed", "level": "Critical"},
            4605: {"code": "Queue Operation Failed", "level": "Critical"},
        }

    def generate_log(self, status: int, message: str, task: str, data: dict, start_time: float):
        self.log_status_value.value = status
        log = self._logic_list.get(status, {"code": None, "level": None})
        
        duration = round((time.time() - start_time) * 1000)
        time_utc_now = datetime.datetime.now(pytz.utc)
        time_local_id = time_utc_now.astimezone(pytz.timezone('Asia/Jakarta'))
        
        hasil = {
            "target": "GRAFANA",
            "timestamp": {
                "utc": str(time_utc_now),
                "local_time": str(time_local_id)
            },
            "service": self.service_name,
            "version": "search",
            "level": log["level"],
            "task": task,
            "status": status,
            "code": log["code"],
            "message": message,
            "duration_ms": duration,
            "data": data
        }
        
        self._file_logger.info(json.dumps(hasil))