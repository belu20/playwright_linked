# Standard Library Imports
import os
import time

# Third-Party Imports
import requests
from dotenv import load_dotenv


load_dotenv()

# Default nilai lokal supaya tidak KeyError kalau .env belum lengkap (mode lokal/testing)
os.environ.setdefault("TYPE", "login")
os.environ.setdefault("CLIENT_ID", "1")
os.environ.setdefault("LIMIT", "10")
os.environ.setdefault("SOURCE", "linkedin")
os.environ.setdefault("ID_TARGET", "1")
os.environ.setdefault("PORT", "5000")
os.environ.setdefault("VM", "local")
# Get public ip for metadata
def get_public_ip():
	try:
		response = requests.get('https://api.ipify.org?format=json', timeout=5)
		response.raise_for_status()
		ip_info = response.json()
		return ip_info["ip"]		
	except Exception as e:
		print(f"[WARNING] Failed to fetch IP address. Reason: {e}")
		return None

# API MDM Keyword-Hashtag-Profile
# OS from environment server (.env)
API_MDM_BASE_URL = os.getenv("API_MDM_BASE_URL")

# OS from docker-compose environment
TYPE = os.environ["TYPE"]
CLIENT_ID = os.environ["CLIENT_ID"]
LIMIT = os.environ["LIMIT"]
SOURCE = os.environ["SOURCE"]
PAGE = os.environ["ID_TARGET"]

# URL API MDM
list_api_keyword = (
    f"{API_MDM_BASE_URL}/api/v1/crawler/keyword-hashtag"
    f"?client_id={CLIENT_ID}"
    f"&limit={LIMIT}"
    f"&source={SOURCE}"
    f"&page={PAGE}"
    f"&for=client"
)


# ===================================================================================================================================================




# Kafka
kafka_location = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
kafka_topic_post = os.getenv("KAFKA_TOPIC_POST")

# Git Metadata
def get_git_commit_id():
	try:
		import subprocess
		commit = subprocess.check_output(
			['git', 'rev-parse', 'HEAD'],
			stderr=subprocess.DEVNULL
		).decode('utf-8').strip()
		if commit:
			return commit
	except Exception:
		pass
	return os.getenv("GIT_COMMIT_ID") or None

git_commit_id = get_git_commit_id()

# Server Info
server_ip = get_public_ip()


# ===================================================================================================================================================