import os
import sys
import time
import random
import signal
import urllib.parse
from multiprocessing import Process, Value

# Load configurations and constants from setting.py
from setting import server_ip, git_commit_id, list_api_keyword, kafka_location, kafka_topic_post

# Import OOP classes
from src.logger import Logger
from src.account import AccountManager
from src.publisher import DataPublisher
from src.target import TargetManager
from src.crawler import LinkedInCrawler

# Hardcoded keywords as requested by local mode
HARDCODED_KEYWORDS = [
    "DCC",
    "NOSÉ Herbal",
    "Loker apotek",
    "digital marketing tips",
]

def run_web_service(port_num: int, git_commit_id_str: str, log_status_val):
    """
    Instantiates and runs the web service inside the child process.
    This prevents PicklingError of Flask objects on Windows.
    """
    from src.web_service import CrawlerWebService
    web_service = CrawlerWebService(
        port_num=port_num,
        git_commit_id=git_commit_id_str,
        log_status_value=log_status_val
    )
    web_service.run()

def ensure_flask_process(flask_process, port_num: int, git_commit_id_str: str, log_status_val):
    """
    Supervisor watchdog: memastikan proses web service Flask selalu hidup
    agar endpoint /status tidak crash dan docker healthcheck selalu lulus.
    """
    if flask_process is None or not flask_process.is_alive():
        print("[WARNING] Flask web service process is not running. Starting/Restarting...")
        try:
            if flask_process is not None:
                flask_process.terminate()
                flask_process.join(timeout=2)
        except Exception:
            pass
        new_process = Process(
            target=run_web_service,
            args=(port_num, git_commit_id_str, log_status_val)
        )
        new_process.daemon = True
        new_process.start()
        print(f"[INFO] Flask web service started on PID {new_process.pid} (port {port_num})")
        return new_process
    return flask_process

def interruptible_sleep(seconds: int, step: int = 5, on_step=None):
    """
    Sleep bertahap agar tidak blocking total dan responsif terhadap interrupt/heartbeat.
    """
    start = time.time()
    while time.time() - start < seconds:
        if on_step:
            try:
                on_step()
            except Exception:
                pass
        remaining = int(seconds - (time.time() - start))
        if remaining <= 0:
            break
        time.sleep(min(step, remaining))

if __name__ == '__main__':
    # Initialize shared multiprocessing state for Flask API
    log_status_value = Value('i', -1)
    web_port = int(os.environ.get("PORT", 5000))
    
    # Initialize OOP Managers
    logger = Logger(
        service_name="linkedin-" + os.environ.get("VM", "local"),
        vm_name=os.environ.get("VM", "local"),
        log_status_value=log_status_value
    )
    
    account_manager = AccountManager(
        accounts_file="accounts.json",
        client_id=int(os.environ.get("CLIENT_ID", 1))
    )
    
    publisher = DataPublisher(
        kafka_location=kafka_location,
        kafka_topic_post=kafka_topic_post,
        logger=logger
    )
    
    target_manager = TargetManager(
        client_id=int(os.environ.get("CLIENT_ID", 1))
    )
    
    crawler = LinkedInCrawler(
        account_manager=account_manager,
        logger=logger,
        publisher=publisher,
        client_id=int(os.environ.get("CLIENT_ID", 1))
    )
    
    # Setup and start the Flask web service in a background process
    flask_process = None
    flask_process = ensure_flask_process(flask_process, web_port, git_commit_id, log_status_value)
    
    # Handle graceful exit
    def signal_handler(sig, frame):
        print(f"\n[INFO] Signal {sig} received, shutting down gracefully...")
        try:
            crawler.close_driver()
        except Exception:
            pass
        try:
            if flask_process and flask_process.is_alive():
                flask_process.terminate()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal_handler)

    # Initialize crawler driver
    print("[INFO] Initializing browser driver...")
    crawler.init_driver()
    
    # Perform initial login with self-healing retry loop
    login_status = crawler.login()
    while login_status != 1:
        print("[WARNING] Initial login failed. Retrying in 30 seconds... Web service remains active.")
        flask_process = ensure_flask_process(flask_process, web_port, git_commit_id, log_status_value)
        interruptible_sleep(30)
        try:
            crawler.restart_driver()
            login_status = crawler.login()
        except Exception as e:
            print(f"[ERROR] Error during login retry: {e}")
        
    try:
        while True:
            flask_process = ensure_flask_process(flask_process, web_port, git_commit_id, log_status_value)
            
            # Refresh start time for logging duration calculation
            crawler.start_time = time.time()
            
            import requests
            print(f"[INFO] Fetching keywords from MDM: {list_api_keyword}")
            try:
                resp = requests.get(list_api_keyword, timeout=20)
                resp.raise_for_status()
                resp_json = resp.json()
                api_data = resp_json.get("data", {})
                if isinstance(api_data, dict):
                    raw_items = api_data.get("data", [])
                else:
                    raw_items = []
                keywords = [item["query"] for item in raw_items if item.get("query")]
                print(f"[INFO] Fetched {len(keywords)} keywords from MDM.")
                if not keywords:
                    print("[WARNING] MDM API returned empty keyword list, using fallback.")
                    keywords = HARDCODED_KEYWORDS
            except Exception as e:
                print(f"[ERROR] Failed to fetch keywords from MDM: {e}. Using fallback.")
                keywords = HARDCODED_KEYWORDS

            # Fetch and shuffle search targets
            targets = target_manager.get_targets(keywords)

            # Restart Chrome every N keywords to release accumulated memory.
            # Session persists via the profile's user-data-dir, so no re-login needed.
            RESTART_DRIVER_EVERY_N_KEYWORDS = int(os.environ.get("RESTART_DRIVER_EVERY_N_KEYWORDS", 5))
            keyword_counter = 0

            for target in targets:
                flask_process = ensure_flask_process(flask_process, web_port, git_commit_id, log_status_value)
                keyword = target["keyword"]
                scroll = target["scroll"]
                
                print("=" * 60)
                print(f"[INFO] Start Crawling: {urllib.parse.unquote(keyword)}")
                print("=" * 60)
                
                try:
                    crawler.crawling(
                        keyword=keyword,
                        scroll=scroll,
                        server_ip=server_ip,
                        git_commit_id=git_commit_id
                    )
                except Exception as crawl_err:
                    print(f"[ERROR] Crawling encountered an error: {crawl_err}")
                    print("[INFO] Attempting driver restart for self-healing...")
                    try:
                        crawler.restart_driver()
                    except Exception as rst_err:
                        print(f"[ERROR] Failed to restart driver: {rst_err}")

                keyword_counter += 1
                if keyword_counter % RESTART_DRIVER_EVERY_N_KEYWORDS == 0:
                    try:
                        crawler.restart_driver()
                    except Exception as rst_err:
                        print(f"[WARNING] Driver periodic restart warning: {rst_err}")
                
                random_sleep = random.randint(60, 90)
                print(f"[INFO] Waiting for {random_sleep} seconds before starting the next target.")
                interruptible_sleep(
                    random_sleep,
                    step=5,
                    on_step=lambda: ensure_flask_process(flask_process, web_port, git_commit_id, log_status_value)
                )
                
            LOOP_SLEEP_SECONDS = int(os.environ.get("LOOP_SLEEP_SECONDS", 600))
            print(f"[INFO] Batch completed. Standby for {LOOP_SLEEP_SECONDS} seconds before the next loop.")
            
            start_loop_wait = time.time()
            while time.time() - start_loop_wait < LOOP_SLEEP_SECONDS:
                flask_process = ensure_flask_process(flask_process, web_port, git_commit_id, log_status_value)
                rem = int(LOOP_SLEEP_SECONDS - (time.time() - start_loop_wait))
                if rem <= 0:
                    break
                if rem % 60 < 5:
                    print(f"[HEARTBEAT] Engine standby: ~{rem}s remaining before next crawl cycle. Web service is active.")
                time.sleep(min(10, max(1, rem)))
            
    except KeyboardInterrupt:
        print("[INFO] Stopped by user.")
    finally:
        print("[INFO] Cleaning up resources...")
        try:
            crawler.close_driver()
        except Exception:
            pass
        try:
            if flask_process and flask_process.is_alive():
                flask_process.terminate()
                flask_process.join(timeout=2)
        except Exception:
            pass