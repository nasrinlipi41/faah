import datetime
import hashlib
import hmac
import html as html_lib
import json
import os
import random
import re
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from flask import Flask, render_template, request, jsonify, session, send_file
from flask_socketio import SocketIO, emit
import requests as req_lib

try:
    import certifi
    from pymongo import MongoClient
except ImportError:
    certifi = None
    MongoClient = None

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

STUDENT_ENDPOINT = (
    "https://results.dinajpurboard.gov.bd/fast/student"
    "?roll={roll}&exam=1"
    "&exp=1787224774"
    "&t=769debce061f8471859fb4cd1069e0454aae3b18294e70c8454edd2fc416320a"
)
INSTITUTE_ENDPOINT = "https://results.dinajpurboard.gov.bd/search/institute"

MAX_RETRIES = 5
CONCURRENCY = 20
DELAY = 0.3
USER_CONCURRENCY = 3
USER_DELAY = 1.5

CONNECT_TIMEOUT = 4
READ_TIMEOUT = 7

# ─── MONGODB SETUP ───
mongo_uri = os.environ.get("MONGO_URI")
db_name = os.environ.get("MONGO_DB_NAME")
history_col = None

if MongoClient and mongo_uri:
    try:
        tls_kwargs = {"tlsCAFile": certifi.where()} if certifi else {}
        mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000, **tls_kwargs)
        db = mongo_client[db_name]
        history_col = db["scrape_history"]
        print("Connected to MongoDB successfully.")
    except Exception as e:
        print(f"MongoDB connection failed: {e}")
        history_col = None
else:
    print("MongoDB URI not provided or pymongo not installed.")


def log_scrape_event(event_type, user_type, details):
    if history_col is None:
        return
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        history_col.insert_one({
            "type": event_type,
            "user_type": user_type,
            "timestamp": now,
            "date_str": now.strftime("%Y-%m-%d"),
            "details": details,
        })
    except Exception as e:
        print(f"Failed to log to MongoDB: {e}")


def get_dashboard_stats():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    stats = {
        "db_connected": history_col is not None,
        "today_str": today_str,
        "total_institutes": 0,
        "today_institutes": 0,
        "total_students": 0,
        "today_students": 0,
        "total_individual_rolls": 0,
        "today_individual_rolls": 0,
        "total_roll_fetches": 0,
        "today_roll_fetches": 0,
        "admin_operations": 0,
        "guest_operations": 0,
        "today_admin_operations": 0,
        "today_guest_operations": 0,
        "recent_logs": [],
    }

    if history_col is None:
        return stats

    try:
        cursor = history_col.find().sort("timestamp", -1)
        logs = list(cursor)
        for doc in logs:
            is_today = (doc.get("date_str") == today_str)
            user_type = doc.get("user_type", "guest")
            event_type = doc.get("type")
            details = doc.get("details", {})

            if user_type == "admin":
                stats["admin_operations"] += 1
                if is_today:
                    stats["today_admin_operations"] += 1
            else:
                stats["guest_operations"] += 1
                if is_today:
                    stats["today_guest_operations"] += 1

            if event_type == "institute":
                stats["total_institutes"] += 1
                students = int(details.get("students_count") or details.get("scraped") or 0)
                stats["total_students"] += students
                if is_today:
                    stats["today_institutes"] += 1
                    stats["today_students"] += students

            elif event_type == "individual":
                rolls = int(details.get("roll_count") or 0)
                stats["total_individual_rolls"] += rolls
                if is_today:
                    stats["today_individual_rolls"] += rolls

            elif event_type == "roll_fetcher":
                stats["total_roll_fetches"] += 1
                if is_today:
                    stats["today_roll_fetches"] += 1

        for doc in logs[:60]:
            ts = doc.get("timestamp")
            formatted_ts = ts.strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(ts, datetime.datetime) else str(ts)
            stats["recent_logs"].append({
                "id": str(doc.get("_id")),
                "type": doc.get("type"),
                "user_type": doc.get("user_type"),
                "timestamp": formatted_ts,
                "details": doc.get("details", {}),
            })
    except Exception as e:
        print(f"Error computing MongoDB stats: {e}")

    return stats


PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http,https&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt",
    "https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt",
]

PROXY_VALIDATION_URL = "https://results.dinajpurboard.gov.bd/"
PROXY_VALIDATE_TIMEOUT = 6
PROXY_VALIDATE_WORKERS = 40
PROXY_VALIDATE_MAX = 200
PROXY_MIN_POOL_SIZE = 5

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

cookie_store = {"value": ""}
active_jobs = {}
download_registry = {}


def is_job_cancelled(job_id):
    if not job_id:
        return False
    return bool(active_jobs.get(job_id, {}).get("cancelled", False))


class ProxyPool:
    def __init__(self):
        self.proxies = []
        self.lock = Lock()
        self._fetching = False

    def fetch_proxies(self, job_id=None):
        with self.lock:
            if self._fetching:
                return
            self._fetching = True
        try:
            candidates = set()
            for url in PROXY_SOURCES:
                if is_job_cancelled(job_id):
                    return
                try:
                    resp = req_lib.get(url, timeout=6)
                    if resp.status_code == 200:
                        for line in resp.text.splitlines():
                            line = line.strip()
                            if line and not line.startswith("#"):
                                if re.match(r"^\d{1,3}(\.\d{1,3}){3}:\d+$", line):
                                    candidates.add(line)
                except Exception:
                    pass
            candidates = list(candidates)
            random.shuffle(candidates)
            candidates = candidates[:PROXY_VALIDATE_MAX]
            valid_proxies = []

            def validate(proxy_str):
                if is_job_cancelled(job_id):
                    return None
                proxies = {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}
                try:
                    r = req_lib.get(PROXY_VALIDATION_URL, proxies=proxies, timeout=PROXY_VALIDATE_TIMEOUT)
                    if r.status_code == 200:
                        return proxy_str
                except Exception:
                    pass
                return None

            executor = ThreadPoolExecutor(max_workers=PROXY_VALIDATE_WORKERS)
            try:
                futures = [executor.submit(validate, p) for p in candidates]
                for fut in as_completed(futures):
                    if is_job_cancelled(job_id):
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    res = fut.result()
                    if res:
                        valid_proxies.append(res)
                        if len(valid_proxies) >= 30:
                            break
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            with self.lock:
                self.proxies.extend(valid_proxies)
                self.proxies = list(set(self.proxies))
        finally:
            with self.lock:
                self._fetching = False

    def get_proxy(self, job_id=None):
        if is_job_cancelled(job_id):
            return None
        with self.lock:
            size = len(self.proxies)
            if size > 0:
                p = self.proxies.pop(0)
                self.proxies.append(p)
                if size <= PROXY_MIN_POOL_SIZE and not self._fetching:
                    import threading
                    threading.Thread(target=self.fetch_proxies, daemon=True).start()
                return p
        with self.lock:
            already_fetching = self._fetching
        if not already_fetching:
            self.fetch_proxies(job_id=job_id)
        else:
            for _ in range(10):
                if is_job_cancelled(job_id):
                    return None
                time.sleep(0.4)
                with self.lock:
                    if self.proxies:
                        p = self.proxies.pop(0)
                        self.proxies.append(p)
                        return p
                if not self._fetching:
                    break
        with self.lock:
            if self.proxies:
                p = self.proxies.pop(0)
                self.proxies.append(p)
                return p
        return None

    def pool_size(self):
        with self.lock:
            return len(self.proxies)

    def remove_proxy(self, proxy_str):
        with self.lock:
            if proxy_str in self.proxies:
                self.proxies.remove(proxy_str)

    def clear(self):
        with self.lock:
            self.proxies = []


proxy_pool = ProxyPool()


def extract_from_html(html_text):
    roll_m = re.search(r"Roll No</b></td>\s*<td><b>(\d+)</b></td>", html_text, re.IGNORECASE)
    roll = roll_m.group(1).strip() if roll_m else None
    name_m = re.search(r"Name of Student:?</b></td>\s*<td[^>]*><b>([^<]+)</b></td>", html_text, re.IGNORECASE)
    name = name_m.group(1).strip() if name_m else None
    gpa = None
    status = None
    res_m = re.search(r"Result</b></td>\s*<td[^>]*><b>([^<]+)</b></td>", html_text, re.IGNORECASE)
    if res_m:
        val = res_m.group(1).strip()
        gpa_match = re.match(r"^GPA\s*=?\s*([\d.]+)$", val, re.I)
        if gpa_match:
            try:
                gpa = float(gpa_match.group(1))
            except ValueError:
                pass
            status = "PASSED"
        else:
            val_upper = val.upper()
            if "FAIL" in val_upper or "F1" in val_upper:
                status = "FAILED"
            elif "PASS" in val_upper:
                status = "PASSED"
    mark = None
    mark_m = re.search(r"TOTAL MARK</b></td>\s*<td[^>]*><b>(\d+)</b></td>", html_text, re.IGNORECASE)
    if mark_m:
        try:
            mark = int(mark_m.group(1))
        except ValueError:
            pass
    school_m = re.search(r"Name of Institute</b></td>\s*<td[^>]*>\s*([^<]+)\s*</td>", html_text, re.IGNORECASE)
    school_raw = html_lib.unescape(school_m.group(1).strip()) if school_m else None
    group_m = re.search(r"Group</b></td>\s*<td[^>]*>\s*([^<]+)\s*</td>", html_text, re.IGNORECASE)
    group = group_m.group(1).strip() if group_m else None
    grades = {}
    for m in re.finditer(
        r'<td class="l">(.+?)</td>\s*<td class="c"><span class="grade-mark">'
        r'<span class="grade-letter">([^<]+)</span>\s*<span class="grade-mod">([^<]*)</span>',
        html_text, re.IGNORECASE,
    ):
        subject = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1)).strip())
        grade = m.group(2).strip() + m.group(3).strip()
        grades[subject] = grade
    if status == "FAILED":
        if gpa is None:
            gpa = 0.0
        if mark is None:
            mark = 0
    return {
        "roll": roll, "name": name, "gpa": gpa, "mark": mark,
        "status": status, "group": group, "school": school_raw, "grades": grades,
    }


def download_html_direct(session, roll, delay):
    url = STUDENT_ENDPOINT.format(roll=roll)
    last_err = "unknown_error"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if resp.status_code == 200:
                return resp.text, None
            elif resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_err = f"HTTP_{resp.status_code}"
                time.sleep(delay * (2 ** attempt))
                continue
            else:
                return None, f"HTTP_{resp.status_code}"
        except (req_lib.exceptions.Timeout, req_lib.exceptions.ConnectionError) as e:
            last_err = type(e).__name__
            time.sleep(delay * (2 ** attempt))
        except Exception as e:
            last_err = type(e).__name__
            time.sleep(delay)
    return None, last_err


def download_html_proxy(session, roll, delay, job_id=None):
    url = STUDENT_ENDPOINT.format(roll=roll)
    last_err = "unknown_error"
    attempt = 1
    no_proxy_waits = 0
    while attempt <= MAX_RETRIES:
        if is_job_cancelled(job_id):
            return None, "cancelled"
        proxy = proxy_pool.get_proxy(job_id=job_id)
        if not proxy:
            if is_job_cancelled(job_id):
                return None, "cancelled"
            no_proxy_waits += 1
            if no_proxy_waits <= 5:
                time.sleep(0.5)
                continue
            return None, "no_working_proxies"
        proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        try:
            resp = session.get(url, proxies=proxies, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if resp.status_code == 200:
                return resp.text, None
            elif resp.status_code == 429 or 500 <= resp.status_code < 600:
                proxy_pool.remove_proxy(proxy)
                last_err = f"HTTP_{resp.status_code}"
                if is_job_cancelled(job_id):
                    return None, "cancelled"
                time.sleep(delay * (1.5 ** attempt))
                attempt += 1
                continue
            else:
                return None, f"HTTP_{resp.status_code}"
        except (req_lib.exceptions.Timeout, req_lib.exceptions.ConnectionError) as e:
            proxy_pool.remove_proxy(proxy)
            last_err = type(e).__name__
            if is_job_cancelled(job_id):
                return None, "cancelled"
            time.sleep(delay * (1.5 ** attempt))
            attempt += 1
        except Exception as e:
            proxy_pool.remove_proxy(proxy)
            last_err = type(e).__name__
            if is_job_cancelled(job_id):
                return None, "cancelled"
            time.sleep(delay)
            attempt += 1
    return None, last_err


def fetch_one(roll, delay=DELAY, use_proxy=False, job_id=None):
    if is_job_cancelled(job_id):
        return {"roll": roll, "ok": False, "error": "cancelled"}
    session = req_lib.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    if use_proxy:
        html_text, err = download_html_proxy(session, roll, delay, job_id=job_id)
    else:
        html_text, err = download_html_direct(session, roll, delay)
    if err:
        return {"roll": roll, "ok": False, "error": err}
    parsed = extract_from_html(html_text)
    if not parsed.get("name"):
        return {"roll": roll, "ok": False, "error": "not_found"}
    return {"roll": roll, "ok": True, "record": parsed}


def fetch_rolls_from_eiin(eiin, cookie_str=None):
    session = req_lib.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:153.0) Gecko/20100101 Firefox/153.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if cookie_str:
        cookie_str = cookie_str.strip().strip("'\"")
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                v = v.strip().encode("ascii", "ignore").decode("ascii")
                session.cookies.set(k.strip(), v, domain="results.dinajpurboard.gov.bd")
    try:
        r0 = session.get(INSTITUTE_ENDPOINT, allow_redirects=True, timeout=15)
        if "challenge" in r0.url or "Human Check" in r0.text or "challenge-form" in r0.text:
            return None, "Blocked by human check / Cloudflare. Update cookies."
        token_m = re.search(r'name="_token"\s+value="([^"]+)"', r0.text)
        if not token_m:
            return None, "Failed to extract CSRF token. Update cookies."
        token = token_m.group(1)
        resp = session.post(
            INSTITUTE_ENDPOINT,
            data={"_token": token, "eiin_no": eiin, "submit": "1"},
            headers={
                "Referer": INSTITUTE_ENDPOINT,
                "Origin": "https://results.dinajpurboard.gov.bd",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            allow_redirects=True, timeout=20,
        )
        if resp.status_code != 200:
            return None, f"HTTP Error {resp.status_code}"
        if "result-check" in resp.url or "Institute Result" not in resp.text:
            return None, "Session expired or blocked. Update cookies."
        return resp.text, None
    except Exception as e:
        return None, str(e)


def parse_institute_name(html_text):
    m = re.search(r'<td colspan="3"><b>([^<]+)</b></td>', html_text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def parse_district(html_text):
    m = re.search(r"District</td>\s*<td>([^<]+)</td>", html_text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def parse_rolls(html_text):
    return re.findall(r'<span class="rest-item">(\d+)\[[\d.]+\]', html_text)


def format_duration(seconds):
    seconds = max(0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {secs}s"


# ─── ROUTES ───

def generate_admin_token():
    return hmac.new(app.secret_key.encode(), b"admin_auth_token_dinajpur", hashlib.sha256).hexdigest()


def is_admin_authorized(req_data=None):
    if session.get("is_admin", False):
        return True
    try:
        if request:
            header_token = request.headers.get("X-Admin-Token")
            if header_token and hmac.compare_digest(str(header_token), generate_admin_token()):
                return True
    except Exception:
        pass
    if isinstance(req_data, dict):
        body_token = req_data.get("admin_token")
        if body_token and hmac.compare_digest(str(body_token), generate_admin_token()):
            return True
    return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["is_admin"] = True
        token = generate_admin_token()
        return jsonify({"ok": True, "message": "Admin login successful.", "is_admin": True, "admin_token": token})
    return jsonify({"ok": False, "message": "Invalid admin username or password."}), 401


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("is_admin", None)
    return jsonify({"ok": True, "message": "Logged out.", "is_admin": False})


@app.route("/api/admin/status", methods=["GET"])
def api_admin_status():
    is_adm = is_admin_authorized()
    token = generate_admin_token() if is_adm else None
    return jsonify({"is_admin": is_adm, "admin_token": token})


@app.route("/api/admin/stats", methods=["GET"])
def api_admin_stats():
    if not is_admin_authorized():
        return jsonify({"ok": False, "message": "Admin access required."}), 403
    stats = get_dashboard_stats()
    return jsonify({"ok": True, "stats": stats})


@app.route("/api/admin/clear_history", methods=["POST"])
def api_admin_clear_history():
    data = request.get_json(silent=True) or {}
    if not is_admin_authorized(data):
        return jsonify({"ok": False, "message": "Admin access required."}), 403
    if history_col is not None:
        try:
            result = history_col.delete_many({})
            return jsonify({"ok": True, "message": f"Cleared {result.deleted_count} history records.", "deleted_count": result.deleted_count})
        except Exception as e:
            return jsonify({"ok": False, "message": f"Failed to clear history: {e}"}), 500
    return jsonify({"ok": True, "message": "History cleared.", "deleted_count": 0})


@app.route("/api/cookie", methods=["GET", "POST"])
def api_cookie():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        cookie_str = data.get("cookie", "").strip()
        if cookie_str:
            cookie_store["value"] = cookie_str
            return jsonify({"ok": True, "message": "Cookie saved."})
        return jsonify({"ok": False, "message": "Empty cookie."}), 400
    else:
        val = cookie_store["value"]
        preview = val[:80] + "..." if len(val) > 80 else val
        return jsonify({"ok": True, "cookie": preview if val else None})


@app.route("/api/roll", methods=["POST"])
def api_roll():
    data = request.get_json(silent=True) or {}
    is_admin = is_admin_authorized(data)
    concurrency = CONCURRENCY if is_admin else USER_CONCURRENCY
    req_delay = DELAY if is_admin else USER_DELAY

    raw = data.get("rolls", "")
    rolls = list(dict.fromkeys(re.findall(r"\d+", raw)))
    if not rolls:
        return jsonify({"ok": False, "message": "No valid roll numbers."}), 400
    results = []
    with ThreadPoolExecutor(max_workers=min(concurrency, len(rolls))) as pool:
        futures = {pool.submit(fetch_one, r, req_delay, False): r for r in rolls}
        for fut in as_completed(futures):
            results.append(fut.result())

    success_count = sum(1 for r in results if r.get("ok"))
    log_scrape_event("individual", "admin" if is_admin else "guest", {
        "roll_count": len(rolls),
        "success_count": success_count,
        "failed_count": len(rolls) - success_count,
        "rolls": rolls[:100],
    })

    return jsonify({"ok": True, "results": results, "is_admin": is_admin})


@app.route("/api/rolls_fetch", methods=["POST"])
def api_rolls_fetch():
    data = request.get_json(silent=True) or {}
    is_admin = is_admin_authorized(data)
    raw = str(data.get("eiin", "") or data.get("eiins", "")).strip()
    eiins = list(dict.fromkeys(re.findall(r"\d+", raw)))
    if not eiins:
        return jsonify({"ok": False, "message": "No valid EIIN provided."}), 400

    results = []
    for eiin in eiins:
        html_text, err = fetch_rolls_from_eiin(eiin, cookie_store["value"] or None)
        if err or not html_text:
            results.append({
                "eiin": eiin,
                "ok": False,
                "message": err or "Failed to fetch institute data.",
            })
            continue
        institute = parse_institute_name(html_text)
        district = parse_district(html_text)
        rolls = parse_rolls(html_text)
        results.append({
            "eiin": eiin,
            "ok": True,
            "institute": institute,
            "district": district,
            "total": len(rolls),
            "rolls": rolls,
        })
        log_scrape_event("roll_fetcher", "admin" if is_admin else "guest", {
            "eiin": eiin,
            "institute": institute,
            "district": district,
            "rolls_count": len(rolls),
        })
    return jsonify({"ok": True, "results": results})


@app.route("/api/download/<file_id>")
def download_json(file_id):
    path = download_registry.get(file_id)
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "message": "File not found."}), 404
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


# ─── SOCKETIO EVENTS ───

@socketio.on("start_institute_scrape")
def handle_institute_scrape(data):
    data_dict = data if isinstance(data, dict) else {}
    is_admin = is_admin_authorized(data_dict)
    concurrency = CONCURRENCY if is_admin else USER_CONCURRENCY
    req_delay = DELAY if is_admin else USER_DELAY

    raw = str(data_dict.get("eiin", "") or data_dict.get("eiins", "")).strip()
    eiins = list(dict.fromkeys(re.findall(r"\d+", raw)))
    if not eiins:
        emit("scrape_error", {"message": "No valid EIIN numbers provided."})
        return

    # Check if cookies are set
    if not cookie_store.get("value"):
        emit("scrape_error", {"message": "No cookie found. Please go to the Cookie tab and save your session cookie first."})
        return

    job_id = str(uuid.uuid4())[:8]
    active_jobs[job_id] = {"cancelled": False}
    total_institutes = len(eiins)
    created_files = []
    emit("scrape_started", {"job_id": job_id, "total_institutes": total_institutes, "is_admin": is_admin})

    # Step 1: Validate cookie & pre-fetch roll lists BEFORE fetching proxies
    emit("scrape_progress", {
        "stage": "Validating session cookies & fetching institute roll list(s)...",
        "done": 0, "total": 0,
        "institute_index": 0, "total_institutes": total_institutes
    })

    valid_institutes = []
    for idx, eiin in enumerate(eiins, 1):
        if active_jobs.get(job_id, {}).get("cancelled"):
            break

        html_text, err = fetch_rolls_from_eiin(eiin, cookie_store["value"])
        if err or not html_text:
            err_msg = err or "Failed to fetch institute data."
            # If cookie error, fail fast and inform user immediately
            if "cookie" in err_msg.lower() or "challenge" in err_msg.lower() or "blocked" in err_msg.lower():
                emit("scrape_error", {"message": f"Cookie validation failed: {err_msg} Please update your cookie in the Cookie tab."})
                return
            emit("institute_error", {
                "eiin": eiin, "message": err_msg,
                "institute_index": idx, "total_institutes": total_institutes
            })
            continue

        institute = parse_institute_name(html_text)
        district = parse_district(html_text)
        rolls = parse_rolls(html_text)
        if not rolls:
            emit("institute_error", {
                "eiin": eiin, "message": "No student rolls found. Check cookie or EIIN.",
                "institute": institute, "district": district,
                "institute_index": idx, "total_institutes": total_institutes
            })
            continue

        valid_institutes.append({
            "idx": idx,
            "eiin": eiin,
            "institute": institute,
            "district": district,
            "rolls": rolls,
        })

    if not valid_institutes:
        if not active_jobs.get(job_id, {}).get("cancelled"):
            emit("scrape_all_complete", {
                "message": "No institutes had valid rolls to scrape.",
                "total_files": 0,
                "zip_file_id": None
            })
        return

    # Step 2: Only now fetch proxies since cookie is confirmed valid!
    if is_job_cancelled(job_id):
        return
    emit("scrape_progress", {
        "stage": "Cookies valid! Fetching fresh proxies... (10-20s)",
        "done": 0, "total": 0,
        "institute_index": 0, "total_institutes": total_institutes
    })
    proxy_pool.clear()
    proxy_pool.fetch_proxies(job_id=job_id)
    if is_job_cancelled(job_id):
        return

    # Step 3: Scrape student results for each valid institute
    for item in valid_institutes:
        if is_job_cancelled(job_id):
            break

        idx = item["idx"]
        eiin = item["eiin"]
        institute = item["institute"]
        district = item["district"]
        rolls = item["rolls"]
        total = len(rolls)
        emit("scrape_progress", {
            "stage": f"[{idx}/{total_institutes}] Scraping {institute} ({total} students)...",
            "done": 0, "total": total,
            "eiin": eiin, "institute": institute, "district": district,
            "institute_index": idx, "total_institutes": total_institutes
        })

        scrape_start_time = time.time()
        records = []

        def scrape_batch(roll_list, label=""):
            nonlocal records
            batch_failures = []
            done = len(records)
            pool = ThreadPoolExecutor(max_workers=concurrency)
            try:
                futures = {pool.submit(fetch_one, r, req_delay, True, job_id): r for r in roll_list}
                for fut in as_completed(futures):
                    if is_job_cancelled(job_id):
                        pool.shutdown(wait=False, cancel_futures=True)
                        return batch_failures
                    res = fut.result()
                    done += 1
                    if res.get("ok"):
                        rec = res["record"]
                        rec["school"] = institute
                        rec["district"] = district
                        records.append(rec)
                    elif res.get("error") != "cancelled":
                        batch_failures.append(res["roll"])
                    if done % 5 == 0 or done == total:
                        if is_job_cancelled(job_id):
                            pool.shutdown(wait=False, cancel_futures=True)
                            return batch_failures
                        socketio.emit("scrape_progress", {
                            "stage": f"[{idx}/{total_institutes}] Scraping{label}...",
                            "done": done, "total": total,
                            "success": len(records), "failures": len(batch_failures),
                            "eiin": eiin, "institute": institute, "district": district,
                            "institute_index": idx, "total_institutes": total_institutes
                        })
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            return batch_failures

        failed_rolls = scrape_batch(rolls)

        if is_job_cancelled(job_id):
            break

        retry = 1
        while failed_rolls and retry <= 3:
            if is_job_cancelled(job_id):
                break
            emit("scrape_progress", {
                "stage": f"[{idx}/{total_institutes}] Retrying {len(failed_rolls)} failed rolls (Attempt {retry}/3)...",
                "done": len(records), "total": total,
                "eiin": eiin, "institute": institute, "district": district,
                "institute_index": idx, "total_institutes": total_institutes
            })
            failed_rolls = scrape_batch(failed_rolls, f" (Retry {retry})")
            retry += 1

        if is_job_cancelled(job_id):
            break

        records.sort(key=lambda r: (r.get("name") or ""))
        elapsed_seconds = time.time() - scrape_start_time
        passed = sum(1 for r in records if r.get("status") == "PASSED")
        failed = sum(1 for r in records if r.get("status") == "FAILED")
        gpa5 = sum(1 for r in records if isinstance(r.get("gpa"), (int, float)) and r.get("gpa") >= 5.0)

        file_id = None
        file_name = None
        if records:
            school_tag = "".join([ch if ch.isalnum() or ch in " _-" else "_" for ch in institute]).strip().replace(" ", "_") or f"EIIN_{eiin}"
            file_name = f"{school_tag}_results.json"
            file_path = os.path.join(DATA_DIR, file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            file_id = str(uuid.uuid4())[:10]
            download_registry[file_id] = file_path
            created_files.append(file_path)

        log_scrape_event("institute", "admin" if is_admin else "guest", {
            "eiin": eiin,
            "institute": institute,
            "district": district,
            "students_count": total,
            "scraped": len(records),
            "passed": passed,
            "failed": failed,
            "gpa5": gpa5,
            "errors": len(failed_rolls),
            "elapsed": format_duration(elapsed_seconds),
            "filename": file_name,
        })

        emit("institute_complete", {
            "eiin": eiin, "institute": institute, "district": district,
            "total": total, "scraped": len(records), "passed": passed,
            "failed": failed, "gpa5": gpa5, "errors": len(failed_rolls),
            "file_id": file_id, "filename": file_name,
            "elapsed": format_duration(elapsed_seconds),
            "institute_index": idx, "total_institutes": total_institutes
        })

    is_cancelled = active_jobs.get(job_id, {}).get("cancelled", False)
    active_jobs.pop(job_id, None)

    zip_file_id = None
    zip_filename = None
    if len(created_files) > 1:
        zip_file_id = str(uuid.uuid4())[:10]
        zip_filename = f"dinajpur_ssc_results_{len(created_files)}_institutes.zip"
        zip_path = os.path.join(DATA_DIR, zip_filename)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in created_files:
                zf.write(fp, arcname=os.path.basename(fp))
        download_registry[zip_file_id] = zip_path

    if is_cancelled:
        emit("scrape_cancelled", {
            "message": "Scraping process was cancelled.",
            "zip_file_id": zip_file_id,
            "zip_filename": zip_filename,
            "total_files": len(created_files),
        })
    else:
        emit("scrape_all_complete", {
            "message": f"Finished scraping {total_institutes} institute(s).",
            "zip_file_id": zip_file_id,
            "zip_filename": zip_filename,
            "total_files": len(created_files),
        })


@socketio.on("cancel_scrape")
def handle_cancel(data):
    job_id = data.get("job_id") if isinstance(data, dict) else None
    if job_id and job_id in active_jobs:
        active_jobs[job_id]["cancelled"] = True
    else:
        for jid in list(active_jobs.keys()):
            active_jobs[jid]["cancelled"] = True
    emit("scrape_cancelled", {"message": "Scraping cancelled by user."})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=10000, debug=False, allow_unsafe_werkzeug=True)