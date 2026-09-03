import datetime
import random
import re
import time
import urllib.parse
import os
import requests
from bs4 import BeautifulSoup
import moment

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from src.gmail_otp_fetcher import get_linkedin_otp_from_gmail


class LinkedInCrawler:
    def __init__(self, account_manager, logger, publisher, client_id: int):
        self.account_manager = account_manager
        self.logger = logger
        self.publisher = publisher
        self.client_id = client_id

        self.playwright = None
        self.context = None
        self.page = None
        self.current_username = None
        self.start_time = time.time()
        self.debug_dir = "debug_image"

    def init_driver(self):
        """
        Inisialisasi Playwright dengan persistent context (setara --user-data-dir
        pada Selenium) sehingga session/cookie tetap tersimpan di disk dan tidak
        perlu login ulang setiap kali driver di-restart.
        """
        profile_dir = os.path.join(
            os.environ.get("CHROME_PROFILE_ROOT", "/app/chrome_profiles"),
            f"client_{self.client_id}"
        )
        os.makedirs(profile_dir, exist_ok=True)

        self.playwright = sync_playwright().start()

        self.context = self.playwright.chromium.launch_persistent_context(
            profile_dir,
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-gpu',
                '--disable-impl-side-painting',
                '--disable-gpu-sandbox',
                '--disable-accelerated-2d-canvas',
                '--disable-accelerated-jpeg-decoding',
                '--test-type=ui',
                '--disable-dev-shm-usage',
                '--ignore-certificate-errors',
                '--allow-running-insecure-content',
                '--disable-features=BackForwardCache',
                '--renderer-process-limit=4',
            ],
            viewport={"width": 1024, "height": 800},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
        )

        # Gunakan halaman yang sudah ada atau buka halaman baru
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = self.context.new_page()

    def close_driver(self):
        if self.page:
            try:
                self.page.close()
            except Exception as e:
                print(f"[WARNING] Error closing page: {e}")
            self.page = None

        if self.context:
            try:
                self.context.close()
            except Exception as e:
                print(f"[WARNING] Error closing context: {e}")
            self.context = None

        if self.playwright:
            try:
                self.playwright.stop()
            except Exception as e:
                print(f"[WARNING] Error stopping playwright: {e}")
            self.playwright = None

    def cleanup_chrome_profile_cache(self):
        """
        Housekeeping berkala: hapus folder cache Chrome yang membengkak,
        TANPA menghapus data session (Cookies, Local Storage, Session Storage,
        Preferences, dll). Harus dipanggil saat driver sedang tertutup
        (setelah close_driver(), sebelum init_driver()) supaya tidak ada
        file yang sedang di-lock oleh proses Chrome yang masih jalan.
        """
        import shutil

        profile_dir = os.path.join(
            os.environ.get("CHROME_PROFILE_ROOT", "/app/chrome_profiles"),
            f"client_{self.client_id}"
        )
        default_dir = os.path.join(profile_dir, "Default")

        # Folder-folder ini aman dihapus karena cuma cache/history,
        # BUKAN tempat session/login disimpan.
        cache_subfolders = [
            "Cache",
            "Code Cache",
            "GPUCache",
            "Service Worker",
            "DawnCache",
            "GrShaderCache",
            "ShaderCache",
        ]

        if not os.path.isdir(default_dir):
            print(f"[INFO] Profile dir belum ada, skip cleanup: {default_dir}")
            return

        total_cleaned = 0
        for folder in cache_subfolders:
            target_path = os.path.join(default_dir, folder)
            if os.path.exists(target_path):
                try:
                    size_mb = 0
                    for root, _, files in os.walk(target_path):
                        for f in files:
                            fp = os.path.join(root, f)
                            try:
                                size_mb += os.path.getsize(fp)
                            except OSError:
                                pass
                    size_mb = round(size_mb / (1024 * 1024), 2)

                    shutil.rmtree(target_path, ignore_errors=True)
                    total_cleaned += size_mb
                    print(f"[INFO] Cleaned Chrome cache folder '{folder}' (~{size_mb} MB)")
                except Exception as e:
                    print(f"[WARNING] Failed to clean '{folder}': {e}")

        print(f"[INFO] Chrome profile cache cleanup finished, total freed ~{round(total_cleaned, 2)} MB")

    def restart_driver(self, cleanup_cache: bool = True):
        """
        Restart browser untuk melepas memory yang menumpuk selama crawling
        panjang. Session tidak hilang karena persistent context tersimpan di disk,
        jadi cukup restore_session() tanpa perlu login form ulang.

        cleanup_cache: kalau True, jalankan housekeeping hapus folder cache
        (aman untuk session) sebelum driver dibuka lagi.
        """
        print("[INFO] Restarting browser to free up memory...")
        self.close_driver()

        if cleanup_cache:
            self.cleanup_chrome_profile_cache()

        self.init_driver()

        if not self.restore_session():
            print("[WARNING] Session not restored after driver restart, re-login diperlukan.")
            self.login()

    def dummy_wait(self, wait_time: int):
        print(f"[INFO] Waiting for {wait_time} second...")
        self.page.wait_for_timeout(wait_time * 1000)

    def logout(self) -> str:
        try:
            self.page.goto("https://www.linkedin.com/m/logout/")
        except Exception as e:
            print("[INFO] Logout failed", e)
        return self.page.content()

    def restore_session(self) -> bool:
        """
        Cek apakah profile yang sudah persist masih punya session LinkedIn
        yang valid, tanpa perlu isi form login.
        Return True kalau session masih valid, False kalau perlu login penuh.
        """
        try:
            self.page.goto("https://www.linkedin.com/feed")
            self.dummy_wait(5)

            if "linkedin.com/feed" in self.page.url:
                print("[INFO] Session restored from existing profile.")
                return True

            global_nav = self.page.query_selector('.global-nav')
            if global_nav:
                print("[INFO] Session restored from existing profile.")
                return True

            print("[INFO] No valid session found in profile, full login required.")
            return False
        except Exception as e:
            print(f"[WARNING] Failed while checking restored session: {e}")
            return False

    def login(self) -> int:
        available_account = None
        try:
            available_account = self.account_manager.get_available_account()
            print(f"[INFO] Available Account: {available_account}")
        except Exception as e:
            print("[ERROR] Failed to read local account file. Please check accounts.json")
            self.logger.generate_log(
                4601,
                "Failed to read local account file.",
                "login",
                {
                    "client_id": str(self.client_id),
                    "error": str(e)
                },
                self.start_time
            )
            return 0

        if available_account is None:
            print("[INFO] No available account or cookie for use")
            self.logger.generate_log(
                4401,
                "No available account or cookie for use.",
                "login",
                {
                    "client_id": str(self.client_id),
                },
                self.start_time
            )
            return 0

        self.current_username = available_account['username']

        # Coba pakai session yang sudah persist dulu sebelum isi form login manual.
        if self.restore_session():
            print("[INFO] Finish login (restored from profile, no form-fill needed)")
            return 1

        try:
            print(f"[INFO] Start login with username: {self.current_username}")
            self.page.goto("https://www.linkedin.com/login")
            self.dummy_wait(5)

            # Selectors
            username_selectors = [
                "input[autocomplete='username webauthn']",
                "input[autocomplete='username']",
                "input[name='session_key']",
                "input[type='email']",
                "#username",
            ]
            password_selectors = [
                "input[autocomplete='current-password']",
                "input[name='session_password']",
                "input[type='password']",
                "#password",
            ]

            def find_first(selectors):
                for sel in selectors:
                    el = self.page.query_selector(sel)
                    if el:
                        return el
                return None

            def fill_field(field, value, label):
                field.scroll_into_view_if_needed()
                self.page.wait_for_timeout(200)
                # Isi field via JS native setter agar React/Vue state terupdate
                self.page.evaluate(
                    """
                    ([el, val]) => {
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(el, val);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    """,
                    [field, value],
                )
                print(f"[INFO] Field {label} filled (JS).")

            print("[INFO] Searching for username field...")
            username_field = find_first(username_selectors)
            if username_field is None:
                # Save screenshot + HTML source for debugging if username field not found
                os.makedirs(self.debug_dir, exist_ok=True)
                self.page.screenshot(path=os.path.join(self.debug_dir, "debug_login_page.png"))
                with open(os.path.join(self.debug_dir, "debug_login_page.html"), "w", encoding="utf-8") as f:
                    f.write(self.page.content())
                print("[WARNING] Username field not found in page. Saved screenshot & HTML.")
            else:
                password_field = find_first(password_selectors)
                if password_field is None:
                    print("[WARNING] Password field not found.")
                else:
                    # Fill fields
                    fill_field(username_field, self.current_username, "username")
                    fill_field(password_field, available_account["password"], "password")

                    # Submit form
                    try:
                        password_field.press("Enter")
                        print("[INFO] Enter key sent natively.")
                    except Exception as e:
                        print(f"[WARNING] Native Enter key failed ({type(e).__name__}), falling back to JS dispatch...")
                        self.page.evaluate(
                            """
                            el => {
                                el.focus();
                                for (const type of ['keydown', 'keypress', 'keyup']) {
                                    el.dispatchEvent(new KeyboardEvent(type, {
                                        key: 'Enter',
                                        code: 'Enter',
                                        keyCode: 13,
                                        which: 13,
                                        bubbles: true,
                                        cancelable: true,
                                    }));
                                }
                            }
                            """,
                            password_field,
                        )
                        print("[INFO] Enter key sent via JS dispatch.")

                    # Wait for redirection/challenge
                    self.page.wait_for_timeout(5000)

                    # Check for checkpoint/challenge
                    if "checkpoint/challenge" in self.page.url:
                        print("\n=== VERIFICATION CHALLENGE (OTP) DETECTED ===")
                        print("URL:", self.page.url)

                        pin_selectors = [
                            "#input__email_verification_pin",
                            "input[name='pin']",
                            "input.input_verification_pin",
                        ]
                        pin_field = find_first(pin_selectors)

                        if pin_field is None:
                            os.makedirs(self.debug_dir, exist_ok=True)
                            self.page.screenshot(path=os.path.join(self.debug_dir, "debug_checkpoint_page.png"))
                            with open(os.path.join(self.debug_dir, "debug_checkpoint_page.html"), "w", encoding="utf-8") as f:
                                f.write(self.page.content())
                            print("[WARNING] Verification PIN field not found. Saved checkpoint debug info.")
                        else:
                            print("[INFO] Mengambil kode OTP dari Gmail...")
                            otp_code = get_linkedin_otp_from_gmail(
                                gmail_user=os.getenv("GMAIL_USER"),
                                gmail_app_password=os.getenv("GMAIL_APP_PASSWORD"),
                                max_wait_seconds=90,
                            )

                            if otp_code is None:
                                print("[WARNING] OTP tidak ditemukan dari Gmail dalam batas waktu. Skip pengisian OTP.")
                                os.makedirs(self.debug_dir, exist_ok=True)
                                self.page.screenshot(path=os.path.join(self.debug_dir, "debug_otp_timeout.png"))
                            else:
                                print(f"[INFO] OTP ditemukan: {otp_code}")
                                fill_field(pin_field, otp_code, "kode OTP")

                                try:
                                    pin_field.press("Enter")
                                    print("[INFO] Enter key sent natively for OTP.")
                                except Exception as e:
                                    print(f"[WARNING] Native Enter for OTP failed ({type(e).__name__}), falling back to JS dispatch...")
                                    self.page.evaluate(
                                        """
                                        el => {
                                            el.focus();
                                            for (const type of ['keydown', 'keypress', 'keyup']) {
                                                el.dispatchEvent(new KeyboardEvent(type, {
                                                    key: 'Enter',
                                                    code: 'Enter',
                                                    keyCode: 13,
                                                    which: 13,
                                                    bubbles: true,
                                                    cancelable: true,
                                                }));
                                            }
                                        }
                                        """,
                                        pin_field,
                                    )
                                print("[INFO] Enter key sent via JS dispatch for OTP.")
                            self.page.wait_for_timeout(5000)

        except Exception as e:
            print(f"[DEBUG] Error pas isi form / OTP: {e}")

        # Polling for login success (redirected to feed or global-nav present)
        timeout = 300
        start_wait = time.time()
        logged_in = False
        print("[INFO] Waiting for user to complete login/captcha in the browser...")

        while time.time() - start_wait < timeout:
            current_url = self.page.url
            if "linkedin.com/feed" in current_url:
                logged_in = True
                break

            global_nav = self.page.query_selector('.global-nav')
            if global_nav:
                logged_in = True
                break

            elapsed = int(time.time() - start_wait)
            if elapsed % 10 == 0:
                print(f"[INFO] Waiting for login/captcha completion... ({elapsed}s elapsed)")
            self.page.wait_for_timeout(2000)

        if logged_in:
            print("[INFO] Login success detected!")
        else:
            print("[WARNING] Login timeout reached.")

        self.page.goto("https://www.linkedin.com/")
        self.dummy_wait(3)

        found = None
        try:
            btn = self.page.query_selector('.nav__button-secondary')
            if btn:
                found = btn.inner_text()
            print("[INFO] Found =>", found)
        except Exception:
            pass

        if found is None:
            status = 1
            print("[INFO] Finish login")
        else:
            status = 0
            print("[INFO] Failed to login, please check the account.")
            self.logger.generate_log(
                4504,
                "Failed to login, please check the account.",
                "login",
                {
                    "client_id": str(self.client_id),
                    "username": self.current_username
                },
                self.start_time
            )
            self.kill_service("Failed to login, please check the account.")

        return status

    def check_login_status(self) -> dict:
        print("[INFO] Check login status")
        self.page.goto("http://www.linkedin.com")

        is_login = True
        found = None
        try:
            btn = self.page.query_selector('.nav__button-secondary')
            if btn:
                found = btn.inner_text()
        except Exception:
            pass

        if found is not None:
            is_login = False

        result = {"is_login": is_login}

        if not is_login:
            self.logger.generate_log(
                4502,
                "Access failed - The status of the search page may be logged out, immediately check the status of the account being used.",
                "login",
                {
                    "client_id": str(self.client_id),
                    "username": self.current_username,
                    "cookies": None,
                },
                self.start_time
            )
            self.kill_service("Access failed - The status of the search page may be logged out, immediately check the status of the account being used.")

        return result

    def do_check_login(self):
        check = self.check_login_status()
        if not check['is_login']:
            try:
                print("[INFO] Relogin..")
                do_login = self.login()
                print(f"[INFO] Do relogin again {do_login}")
                self.page.wait_for_timeout(3000)
            except Exception as e:
                print("[ERROR] Failed login:", e)

    def extract_update_urns_from_dom(self, post_urls: list, seen: set) -> int:
        added = 0
        src = self.page.content() or ""

        ugc_patterns = [
            r'userGeneratedContentId=(\d{19})',
            r'urn:li:ugcPost:(\d{19})',
            r'userGeneratedContentPostUrn=UserGeneratedContentPostUrn\(userGeneratedContentId=(\d{19})\)',
        ]

        share_patterns = [
            r'shareId=(\d{19})',
            r'urn:li:share:(\d{19})',
            r'ShareUrn\(shareId=(\d{19})\)',
        ]

        ugc_ids = set()
        share_ids = set()

        for pattern in ugc_patterns:
            ugc_ids.update(re.findall(pattern, src))

        for pattern in share_patterns:
            share_ids.update(re.findall(pattern, src))

        for ugc_id in ugc_ids:
            raw_post_id = f"urn:li:ugcPost:{ugc_id}"
            url = f"https://www.linkedin.com/feed/update/{raw_post_id}/"

            if url not in seen:
                seen.add(url)
                post_urls.append(url)
                added += 1

        for share_id in share_ids:
            raw_post_id = f"urn:li:share:{share_id}"
            url = f"https://www.linkedin.com/feed/update/{raw_post_id}/"

            if url not in seen:
                seen.add(url)
                post_urls.append(url)
                added += 1

        return added

    def save_debug_screenshot(self, name: str):
        os.makedirs(self.debug_dir, exist_ok=True)
        path = os.path.join(self.debug_dir, name)
        self.page.screenshot(path=path)
        print(f"[DEBUG] Saved screenshot: {path}")

    def scroll_search_results(self) -> bool:
        moved = False
        try:
            workspace = self.page.query_selector("#workspace")
            if workspace:
                before = self.page.evaluate("el => el.scrollTop", workspace)
                self.page.evaluate("el => { el.scrollTop = el.scrollTop + 1200; }", workspace)
                after = self.page.evaluate("el => el.scrollTop", workspace)
                moved = after > before
        except Exception as e:
            print(f"[DEBUG] workspace scroll failed: {e}")

        try:
            buttons = self.page.query_selector_all(
                "xpath=//button[contains(., 'Load more') or contains(., 'Muat lebih banyak')]"
            )
            for btn in buttons:
                try:
                    if btn.is_visible() and btn.is_enabled():
                        btn.scroll_into_view_if_needed()
                        btn.click()
                        return True
                except Exception as e:
                    print(f"[DEBUG] failed clicking one Load more button: {e}")
        except Exception as e:
            print(f"[DEBUG] Load more lookup failed: {e}")

        return moved

    def crawling(self, keyword: str, scroll: bool, server_ip: str, git_commit_id: str):
        self.check_login_status()
        post_urls = []
        max_pagination = 10
        page_count = 0
        seen = set()

        print(f"[INFO] Search Query: {urllib.parse.unquote(keyword)}")

        while True:
            page_count += 1
            search_url = (
                "https://www.linkedin.com/search/results/content/?keywords="
                + keyword
                + "&page="
                + str(page_count)
                + "&sortBy=\"date_posted\"&datePosted=\"past-24h\""
            )
            print(f"[INFO] Search URL: {search_url}")

            try:
                self.page.goto(search_url)
                self.page.wait_for_timeout(5000)

                added = self.extract_update_urns_from_dom(post_urls, seen)
                print(f"[INFO] Added {added} urls (initial), total unique={len(post_urls)}")

                scroll_times = random.randint(5, 10)
                print(f"[INFO] Randomly decided to scroll {scroll_times} times.")

                for i in range(scroll_times):
                    try:
                        moved = self.scroll_search_results()
                        self.page.wait_for_timeout(int(random.uniform(4, 10) * 1000))

                        added = self.extract_update_urns_from_dom(post_urls, seen)
                        print(
                            f"[INFO] Scroll {i+1}/{scroll_times}: "
                            f"moved={moved}, added {added}, total unique={len(post_urls)}"
                        )
                    except Exception as e:
                        print("[ERROR] Failed to scroll the page:", e)
                        break

            except Exception as e:
                print("[ERROR] Failed to crawl:", e)
                break

            if not scroll:
                break
            if page_count == max_pagination:
                break

        print(f"[INFO] Finish collecting post URL for keyword: {urllib.parse.unquote(keyword)}")
        print("=" * 90)

        total_data = 0
        print("[INFO] Start crawling post url.")
        for url in post_urls:
            print(f"[INFO] Post URL: {url}")
            try:
                datetime_crawling_ms = int(datetime.datetime.now().timestamp() * 1000)
                created_time = datetime.datetime.now().isoformat()
                updated_time = None
                hashtag = []
                raw_html = requests.get(url=url).text

                if "telescopeScope" in raw_html:
                    print("\033[33m[INFO] Private post found.\033[0m")
                    print("\033[33m[INFO] Starting to get URL with browser.\033[0m")
                    self.page.goto(url)
                    self.dummy_wait(5)
                    soup = BeautifulSoup(self.page.content(), 'html.parser')
                    mode = "playwright"
                else:
                    soup = BeautifulSoup(raw_html, 'html.parser')
                    mode = "requests"

                print("[INFO] Crawling mode:", mode)
                post_id = url.split(":")[-1].split("?")[0].strip("/")

                # REQUESTS
                if mode == "requests":
                    try:
                        content_str = soup.find(class_="attributed-text-segment-list__container relative mt-1 mb-1.5 babybear:mt-0 babybear:mb-0.5").text
                    except Exception:
                        content_str = None

                    if content_str is None:
                        try:
                            content_str = soup.find(attrs={"data-tracking-control-name": "public_post_feed-article-content"}).text
                        except Exception:
                            content_str = None
                    try:
                        comment_count = int(soup.find(attrs={"data-tracking-control-name": "public_post_social-actions-comments"}).text.replace(" Comments", "").replace(" Comment", "").replace("\n", "").replace(",", "").strip())
                    except Exception:
                        comment_count = 0

                    try:
                        for x in soup.find_all(attrs={"data-tracking-control-name": "   "}):
                            if "#" in x.text:
                                hashtag.append(x.text)
                    except Exception:
                        hashtag = []

                    try:
                        reaction_count = int(soup.find(attrs={"data-test-id": "social-actions__reaction-count"}).text.replace(",", ""))
                    except Exception:
                        reaction_count = 0

                    try:
                        post_owner_name = soup.find(attrs={"data-tracking-control-name": "public_post_feed-actor-name"}).text.replace("\n ", "").replace("\n", "").strip()
                    except Exception:
                        post_owner_name = None

                    try:
                        post_owner_url = soup.find(attrs={"data-tracking-control-name": "public_post_feed-actor-name"}).get("href").split("?")[0]
                    except Exception:
                        post_owner_url = None

                    try:
                        post_owner_headline = soup.find(class_="share-update-card__actor-headline").text.replace("\n", "").strip()
                    except Exception:
                        post_owner_headline = None

                    try:
                        post_owner_pic = soup.find(attrs={"data-ghost-classes": "bg-color-entity-ghost-background"}).get("data-delayed-url")
                    except Exception:
                        post_owner_pic = None

                    try:
                        post_time_str = soup.find("time").text.split("·")[0].replace("\n", "").replace(" ", "").replace("Edited", "").strip()
                    except Exception:
                        post_time_str = None

                    try:
                        if "m" in post_time_str:
                            post_time_datetime = moment.date(post_time_str.replace("m", "minutes ago"))
                        elif "h" in post_time_str:
                            post_time_datetime = moment.date(post_time_str.replace("h", "hours ago"))
                        elif "d" in post_time_str:
                            post_time_datetime = moment.date(post_time_str.replace("d", "days ago"))
                        else:
                            post_time_datetime = moment.date(post_time_str)
                    except Exception as e:
                        post_time_datetime = None
                        print("[ERROR] Failed to get post time:", e)
                    try:
                        post_time_datetimems = int(post_time_datetime.datetime.timestamp() * 1000)
                    except Exception:
                        post_time_datetimems = None

                # PLAYWRIGHT (private post)
                elif mode == "playwright":
                    content_str = None
                    limited_content = ""

                    def print_blue(text):
                        print("\033[34m" + text + "\033[0m")

                    try:
                        el = self.page.query_selector("[class*='update-components-text']")
                        if el:
                            content_str = el.inner_text().strip()
                            content_str = content_str.replace("Tagar", "").strip()
                            words = content_str.split()[:20]
                            limited_content = ' '.join(words) + "..."
                    except Exception as e:
                        print("[ERROR] Failed to get content:", e)
                    print_blue(f"[DEBUG] Post content: {limited_content}")

                    try:
                        el = self.page.query_selector(
                            "xpath=//li[contains(@class, 'social-details-social-counts__comments')]"
                            "//button//span[@aria-hidden='true']"
                        )
                        comment_count = el.inner_text().replace(" Comments", "").replace(" Comment", "").replace(" Komentar", "").replace("\n", "").replace(",", "").strip() if el else 0
                    except Exception:
                        comment_count = 0
                    print_blue(f"[DEBUG] Post comments: {comment_count}")

                    try:
                        hashtag = re.findall(r"#\w+", content_str)
                        hashtag.extend(hashtag)
                    except Exception:
                        hashtag = []
                    print_blue(f"[DEBUG] Post hashtags: {hashtag[:6]}")

                    try:
                        el = self.page.query_selector("[class*='social-details-social-counts__reactions-count']")
                        reaction_count = el.inner_text() if el else 0
                    except Exception:
                        reaction_count = 0
                    print_blue(f"[DEBUG] Post reactions: {reaction_count}")

                    try:
                        el = self.page.query_selector("[class*='update-components-actor__single-line-truncate']")
                        post_owner_name = el.inner_text().replace("\n", "").strip() if el else None
                    except Exception:
                        post_owner_name = None
                    print_blue(f"[DEBUG] Post owner name: {post_owner_name}")

                    try:
                        el = self.page.query_selector("a[class*='update-components-actor__meta-link']")
                        if el:
                            post_owner_url = el.get_attribute("href")
                        else:
                            el = self.page.query_selector("a[class*='update-components-actor__image']")
                            post_owner_url = el.get_attribute("href") if el else None
                    except Exception:
                        post_owner_url = None
                    print_blue(f"[DEBUG] Post owner url: {post_owner_url}")

                    try:
                        el = self.page.query_selector(
                            "[class*='update-components-actor__description'][class*='text-body-xsmall']"
                        )
                        if el:
                            post_owner_headline = el.inner_text().replace("\n", "").strip()
                            if "•" in post_owner_headline:
                                post_owner_headline = None
                        else:
                            post_owner_headline = None
                    except Exception:
                        post_owner_headline = None
                    print_blue(f"[DEBUG] Post owner headline: {post_owner_headline}")

                    try:
                        el = self.page.query_selector(
                            "span.js-update-components-actor__avatar img"
                        )
                        post_owner_pic = el.get_attribute("src") if el else None
                    except Exception:
                        post_owner_pic = None
                    print_blue(f"[DEBUG] Post owner pic: {post_owner_pic}")

                    try:
                        el = self.page.query_selector(
                            "[class*='update-components-actor__sub-description'][class*='text-body-xsmall']"
                        )
                        if el:
                            post_time_str = el.inner_text().split(" •")[0].replace(" • Edited •   ", "").replace(" • Diedit •   ", "").replace("\n", "").strip()
                        else:
                            post_time_str = None
                    except Exception:
                        post_time_str = None
                    print_blue(f"[DEBUG] Post date: {post_time_str}")

                    try:
                        if "mnt" in post_time_str:
                            post_time_datetime = moment.date(post_time_str.replace("mnt", "minutes ago"))
                        elif "jam" in post_time_str:
                            post_time_datetime = moment.date(post_time_str.replace("jam", "hours ago"))
                        elif "hr" in post_time_str:
                            post_time_datetime = moment.date(post_time_str.replace("hr", "days ago"))
                        elif "mgg" in post_time_str:
                            post_time_datetime = moment.date(post_time_str.replace("mgg", "weeks ago"))
                        else:
                            post_time_datetime = moment.date(post_time_str)
                    except Exception as e:
                        post_time_datetime = None
                        print("[ERROR] Failed to get post_time_datetime:", e)

                    try:
                        post_time_datetimems = int(post_time_datetime.datetime.timestamp() * 1000)
                    except Exception:
                        post_time_datetimems = None

                total_data += 1
                print(f"[DEBUG] [{total_data}] {post_id} | {post_time_str}")

                # Metadata Crawling
                metadata = {
                    "crawler": {
                        "server_ip": server_ip,
                        "git_commit_id": git_commit_id,
                        "account": {
                            "user": self.current_username,
                            "token": None
                        },
                        "type": "login",
                        "search": urllib.parse.unquote(keyword),
                        "client_id": int(self.client_id),
                        "platform": "Media Intelligence",
                        "crawling_mode": mode,
                        "author": "macan"
                    }
                }

                # Insert Data LinkedIn Post
                insert_data = {
                    "post_id": post_id,
                    "url": url,
                    "datetime_crawling_ms": datetime_crawling_ms,
                    "owner": {
                        "name": post_owner_name,
                        "url": post_owner_url,
                        "headline": post_owner_headline,
                        "avatar": post_owner_pic
                    },
                    "post": {
                        "content_str": content_str
                    },
                    "post_time": {
                        "post_time_str": post_time_str,
                        "post_time_datetime": str(post_time_datetime.date) if post_time_datetime else None,
                        "post_time_datetimems": post_time_datetimems
                    },
                    "datetime_ms": post_time_datetimems,
                    "hashtag": hashtag,
                    "comment_count": comment_count,
                    "reaction_count": reaction_count,
                    "metadata": metadata,
                    "created_time": created_time,
                    "updated_time": updated_time
                }

                # Send data
                self.publisher.produce_message(post_id, insert_data)

            except Exception as e:
                print("[ERROR] Reason:", e)
            self.page.wait_for_timeout(2000)

        self.logger.generate_log(
            0000,
            "Crawling finished for this loop. Please check the data to review total results.",
            "crawling summary",
            {
                "username": self.current_username,
                "client_id": self.client_id,
                "total_data": total_data,
                "keyword": keyword,
                "ip_server": server_ip
            },
            self.start_time
        )

        return 1

    def kill_service(self, message: str):
        print(message)
        if self.current_username:
            self.account_manager.release_account(self.current_username)
            self.account_manager.mark_account_failed(self.current_username)

        import multiprocessing
        import sys
        for prc in multiprocessing.active_children():
            prc.terminate()
        self.close_driver()
        sys.exit(0)