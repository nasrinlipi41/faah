# 🇧🇩 Dinajpur Board Result Scraper Website 2026

🌐 **Web-based SSC 2026 exam result scraper for Dinajpur Education Board, Bangladesh.**
Built with **Flask, Flask-SocketIO, and Vanilla JavaScript**. 

## ✨ Features

* 🎓 **Individual Roll Lookup**: Check single or multiple student results with complete subject-wise marksheets. 📝
* 🏫 **Multi-Institute Scraping**: Scrape one or multiple institutes via EIIN numbers with real-time progress bar, proxy rotation, automatic retries, and individual JSON exports. 🔄
* 📦 **Batch ZIP Archive**: Automatically bundles multiple scraped institute JSON files into a single downloadable `.zip` archive. 🗜️
* 🔢 **Multi-EIIN Roll Fetcher**: Fetch student roll lists for one or multiple EIINs. 📋
* 🍪 **Cookie Management**: Save and manage browser session cookies for Cloudflare/CSRF bypass. 🔐
* ⚡ **Live Progress & Cancellation**: WebSocket-powered real-time updates with cancel support. 🛑
* 🌙 **Responsive Dark UI**: Clean dark-themed interface for mobile and desktop. 📱💻

## 🛠️ Tech Stack

* 🐍 **Backend**: Python, Flask, Flask-SocketIO
* 🎨 **Frontend**: HTML5, CSS3, JavaScript (ES6+), Socket.IO Client
* 🕷️ **Scraping**: Requests, ThreadPoolExecutor (multi-threaded proxy rotation)

## 📁 Project Structure

```text
├── app.py                  # 🚀 Flask + SocketIO backend
├── requirements.txt        # 📦 Python dependencies
├── templates/
│   └── index.html          # 🖥️ Frontend UI with tabs
├── static/
│   ├── css/
│   │   └── style.css       # 🎨 Dark theme stylesheet
│   └── js/
│       └── app.js          # ⚡ Client-side Socket.IO and REST API handlers
└── data/                   # 📂 JSON output files and generated ZIP archives
```

## 🚀 Usage

### 🎓 Individual Roll Lookup

1. 🌐 Go to the **Individual** tab.
2. 🔢 Enter one or more roll numbers (space or comma separated).
3. 🔍 Click **Fetch Results** to view complete marksheets.

### 🏫 Institute Scraping

1. 🌐 Go to the **Institute** tab.
2. 🏷️ Enter an EIIN number.
3. ▶️ Click **Start Scraping** to begin.
4. 📊 The live progress bar will show the scraping status.
5. 📥 Download the JSON file when scraping completes.

### 🔢 Roll Fetcher

1. 🌐 Go to the **Roll Fetcher** tab.
2. 🏷️ Enter an EIIN number.
3. 🔍 Click **Fetch Rolls** to get the student roll list.

### 🍪 Cookie Setup

1. 🌐 Go to the **Cookie** tab.
2. 📖 Follow the on-screen instructions to extract cookies from your browser.
3. 📋 Paste and save the cookies.
4. 🔐 Cookies may be required for institute roll fetching (EIIN mode).



⚠️ Note: 🛡️ Use the scraper responsibly and respect the target website's terms and rate limits.

## ❤️ Built With

🐍 Python • 🌐 Flask • ⚡ Socket.IO • 🎨 JavaScript
