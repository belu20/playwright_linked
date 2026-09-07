import logging
from flask import Flask, jsonify
from flask_cors import CORS

# Mute logging akses werkzeug agar tidak membanjiri terminal setiap 30 detik
logging.getLogger('werkzeug').setLevel(logging.ERROR)

class CrawlerWebService:
    def __init__(self, port_num: int, git_commit_id: str, log_status_value):
        self.port_num = port_num
        self.git_commit_id = git_commit_id
        self.log_status_value = log_status_value
        self.app = Flask(__name__)
        CORS(self.app)
        self._setup_routes()

    def _setup_routes(self):
        @self.app.route('/status', methods=["GET"])
        def start():
            try:
                status = self.log_status_value.value
                real_status = status if status != -1 else None
                return jsonify({
                    "name": "linkedin",
                    "version": "search",
                    "commit_id": self.git_commit_id,
                    "log_status": real_status,
                    "status": "ok"
                }), 200
            except Exception as e:
                return jsonify({
                    "name": "linkedin",
                    "version": "search",
                    "commit_id": self.git_commit_id,
                    "log_status": None,
                    "error": str(e)
                }), 200

        @self.app.route('/healthz', methods=["GET"])
        def healthz():
            return jsonify({"status": "healthy"}), 200

    def run(self):
        # Bind ke 0.0.0.0 agar bisa diakses oleh docker network, healthcheck, dan external monitoring
        self.app.run(host='0.0.0.0', port=self.port_num, debug=False, threaded=True)

