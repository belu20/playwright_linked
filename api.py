import os
import time
import random
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

if __name__ == '__main__':
    # Initialize shared multiprocessing state for Flask API
    log_status_value = Value('i', -1)
    
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
    flask_process = Process(
        target=run_web_service,
        args=(int(os.environ.get("PORT", 5000)), git_commit_id, log_status_value)
    )
    flask_process.start()
    
    # Initialize crawler driver
    crawler.init_driver()
    
    # Perform initial login
    login_status = crawler.login()
    if login_status != 1:
        crawler.kill_service("[ERROR] Failed to login => Engine shutdown")
        
    try:
        while True:
            # Refresh start time for logging duration calculation
            crawler.start_time = time.time()
            
            import requests
            print(f"[INFO] Fetching keywords from MDM: {list_api_keyword}")
            try:
                resp = requests.get(list_api_keyword, timeout=30)
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
                    crawler.restart_driver()
                
                random_sleep = random.randint(80, 100)
                print(f"[INFO] Waiting for {random_sleep} seconds before starting the next target.")
                time.sleep(random_sleep)
                
            print("[INFO] Please wait 1000 seconds before the next loop.")
            time.sleep(1000)
            
    except KeyboardInterrupt:
        print("[INFO] Stopped by user.")
    finally:
        crawler.close_driver()
        flask_process.terminate()