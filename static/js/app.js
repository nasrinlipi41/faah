const BACKEND_URL = "https://faah-dir2.onrender.com";
const socket = io(BACKEND_URL);
let currentJobId = null;
let isAdmin = false;
let adminToken = sessionStorage.getItem('admin_token') || null;

// ─── TABS ───
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        const targetPanel = document.getElementById(btn.dataset.tab);
        if (targetPanel) {
            targetPanel.classList.add('active');
            if (btn.dataset.tab === 'tab-admin') {
                loadAdminDashboard();
            }
        }
    });
});

// ─── AUTHENTICATION ───
async function checkAuthStatus() {
    try {
        const headers = adminToken ? {'X-Admin-Token': adminToken} : {};
        const res = await fetch("${BACKEND_URL}"+"/api/admin/status", {headers});
        const data = await res.json();
        isAdmin = !!data.is_admin;
        if (data.admin_token) {
            adminToken = data.admin_token;
            sessionStorage.setItem('admin_token', adminToken);
        } else if (!isAdmin) {
            adminToken = null;
            sessionStorage.removeItem('admin_token');
        }
        updateAuthUI();
    } catch (e) {
        console.error('Failed to check auth status', e);
    }
}

function updateAuthUI() {
    const badge = document.getElementById('user-tier-badge');
    const tierText = document.getElementById('tier-text');
    const loginBtn = document.getElementById('admin-login-btn');
    const logoutBtn = document.getElementById('admin-logout-btn');
    const adminTabBtn = document.getElementById('admin-tab-btn');

    if (isAdmin) {
        badge.className = 'badge badge-admin';
        tierText.textContent = '👑 Admin Mode (Full Speed: 20x / 0.3s)';
        loginBtn.style.display = 'none';
        logoutBtn.style.display = 'inline-flex';
        adminTabBtn.style.display = 'inline-block';
    } else {
        badge.className = 'badge badge-guest';
        tierText.textContent = '👤 Guest Mode (Slow Speed: 3x / 1.5s)';
        loginBtn.style.display = 'inline-flex';
        logoutBtn.style.display = 'none';
        adminTabBtn.style.display = 'none';
        // If current tab is admin, switch back to individual
        if (document.getElementById('tab-admin').classList.contains('active')) {
            document.querySelector('[data-tab="tab-individual"]').click();
        }
    }
}

// Modal controls
const adminModal = document.getElementById('admin-modal');
document.getElementById('admin-login-btn').addEventListener('click', () => {
    document.getElementById('admin-login-msg').style.display = 'none';
    document.getElementById('admin-user-input').value = '';
    document.getElementById('admin-pass-input').value = '';
    adminModal.classList.add('visible');
});

document.getElementById('modal-close-btn').addEventListener('click', () => {
    adminModal.classList.remove('visible');
});
document.getElementById('modal-cancel-btn').addEventListener('click', () => {
    adminModal.classList.remove('visible');
});

document.getElementById('admin-login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('admin-user-input').value.trim();
    const password = document.getElementById('admin-pass-input').value.trim();
    const msgEl = document.getElementById('admin-login-msg');

    try {
        const resp = await fetch("${BACKEND_URL}"+"/api/admin/login", {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username, password})
        });
        const data = await resp.json();
        if (data.ok) {
            isAdmin = true;
            if (data.admin_token) {
                adminToken = data.admin_token;
                sessionStorage.setItem('admin_token', adminToken);
            }
            // Reconnect socket so the newly authenticated session is immediately active without refresh
            if (socket && socket.connected) {
                socket.disconnect();
                socket.connect();
            }
            updateAuthUI();
            adminModal.classList.remove('visible');
            document.getElementById('admin-tab-btn').click();
        } else {
            msgEl.textContent = data.message || 'Invalid credentials.';
            msgEl.style.display = 'block';
        }
    } catch (err) {
        msgEl.textContent = 'Login request failed: ' + err.message;
        msgEl.style.display = 'block';
    }
});

document.getElementById('admin-logout-btn').addEventListener('click', async () => {
    try {
        await fetch("${BACKEND_URL}"+"/api/admin/logout", {method: 'POST'});
        isAdmin = false;
        adminToken = null;
        sessionStorage.removeItem('admin_token');
        if (socket && socket.connected) {
            socket.disconnect();
            socket.connect();
        }
        updateAuthUI();
    } catch (e) {
        console.error('Logout error', e);
    }
});

// ─── ADMIN DASHBOARD ───
async function loadAdminDashboard() {
    const dbStatusBar = document.getElementById('db-status-bar');
    const tableBody = document.getElementById('history-table-body');
    dbStatusBar.textContent = 'Loading database analytics...';

    try {
        const headers = adminToken ? {'X-Admin-Token': adminToken} : {};
        const resp = await fetch("${BACKEND_URL}"+"/api/admin/stats", {headers});
        const data = await resp.json();
        if (!data.ok) {
            dbStatusBar.innerHTML = `<span class="msg-error">${data.message}</span>`;
            return;
        }

        const stats = data.stats;
        if (stats.db_connected) {
            dbStatusBar.innerHTML = `🟢 <strong>MongoDB Connected:</strong> Showing live data & history (UTC Date: ${stats.today_str})`;
        } else {
            dbStatusBar.innerHTML = `⚠️ <strong>MongoDB Offline:</strong> Set <code>MONGO_URI</code> environment variable to enable persistent logging.`;
        }

        document.getElementById('stat-total-institutes').textContent = stats.total_institutes.toLocaleString();
        document.getElementById('stat-today-institutes').textContent = stats.today_institutes.toLocaleString();

        document.getElementById('stat-total-students').textContent = stats.total_students.toLocaleString();
        document.getElementById('stat-today-students').textContent = stats.today_students.toLocaleString();

        document.getElementById('stat-total-rolls').textContent = stats.total_individual_rolls.toLocaleString();
        document.getElementById('stat-today-rolls').textContent = stats.today_individual_rolls.toLocaleString();

        document.getElementById('stat-total-fetches').textContent = stats.total_roll_fetches.toLocaleString();
        document.getElementById('stat-today-fetches').textContent = stats.today_roll_fetches.toLocaleString();

        document.getElementById('stat-admin-ops').textContent = stats.admin_operations.toLocaleString();
        document.getElementById('stat-today-admin-ops').textContent = stats.today_admin_operations.toLocaleString();
        document.getElementById('stat-guest-ops').textContent = stats.guest_operations.toLocaleString();
        document.getElementById('stat-today-guest-ops').textContent = stats.today_guest_operations.toLocaleString();

        // Render logs
        if (!stats.recent_logs || stats.recent_logs.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">No scraping activity recorded yet.</td></tr>';
            return;
        }

        tableBody.innerHTML = stats.recent_logs.map(log => {
            const userClass = log.user_type === 'admin' ? 'user-admin' : 'user-guest';
            const userText = log.user_type === 'admin' ? '👑 Admin' : '👤 Guest';
            const typeClass = `type-${log.type}`;

            let targetText = '-';
            let resultText = '-';
            const det = log.details || {};

            if (log.type === 'institute') {
                targetText = `<strong>${det.institute || 'EIIN ' + det.eiin}</strong> (EIIN: ${det.eiin})`;
                resultText = `${det.students_count || 0} students (Pass: ${det.passed || 0}, Fail: ${det.failed || 0}, 5.00: ${det.gpa5 || 0}) • ⏱ ${det.elapsed || '-'}`;
            } else if (log.type === 'individual') {
                targetText = `${det.roll_count || 0} roll(s)`;
                resultText = `OK: ${det.success_count || 0}, Fail: ${det.failed_count || 0}`;
            } else if (log.type === 'roll_fetcher') {
                targetText = `EIIN ${det.eiin} (${det.institute || 'N/A'})`;
                resultText = `${det.rolls_count || 0} rolls found`;
            }

            return `
                <tr>
                    <td style="color:var(--text-dim);font-size:0.8rem">${log.timestamp}</td>
                    <td><span class="user-badge ${userClass}">${userText}</span></td>
                    <td><span class="type-badge ${typeClass}">${log.type}</span></td>
                    <td>${targetText}</td>
                    <td>${resultText}</td>
                </tr>`;
        }).join('');

    } catch (err) {
        dbStatusBar.innerHTML = `<span class="msg-error">Failed to load stats: ${err.message}</span>`;
    }
}

document.getElementById('refresh-stats-btn').addEventListener('click', loadAdminDashboard);

document.getElementById('clear-history-btn').addEventListener('click', async () => {
    if (!confirm('Are you sure you want to permanently clear all scraping history and analytics from the database?')) {
        return;
    }
    const dbStatusBar = document.getElementById('db-status-bar');
    dbStatusBar.textContent = 'Clearing database history...';
    try {
        const headers = {'Content-Type': 'application/json'};
        if (adminToken) headers['X-Admin-Token'] = adminToken;
        const resp = await fetch("${BACKEND_URL}"+"/api/admin/clear_history", {
            method: 'POST',
            headers,
            body: JSON.stringify({admin_token: adminToken})
        });
        const data = await resp.json();
        if (data.ok) {
            await loadAdminDashboard();
            dbStatusBar.innerHTML = `<span style="color:var(--green)">✅ ${data.message}</span>`;
        } else {
            dbStatusBar.innerHTML = `<span class="msg-error">${data.message}</span>`;
        }
    } catch (err) {
        dbStatusBar.innerHTML = `<span class="msg-error">Failed to clear history: ${err.message}</span>`;
    }
});

// ─── INDIVIDUAL ROLL ───
document.getElementById('roll-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const rolls = document.getElementById('roll-input').value.trim();
    if (!rolls) return;
    const btn = e.target.querySelector('button');
    const output = document.getElementById('roll-results');
    btn.disabled = true;
    btn.textContent = 'Fetching...';
    output.innerHTML = '<p class="status-line">Fetching results...</p>';

    try {
        const headers = {'Content-Type': 'application/json'};
        if (adminToken) headers['X-Admin-Token'] = adminToken;
        const resp = await fetch("${BACKEND_URL}"+"/api/roll", {
            method: 'POST',
            headers,
            body: JSON.stringify({rolls, admin_token: adminToken})
        });
        const data = await resp.json();
        if (!data.ok) {
            output.innerHTML = `<p class="msg-error">${data.message}</p>`;
            return;
        }
        output.innerHTML = '';
        data.results.forEach(res => {
            if (res.ok) {
                const r = res.record;
                const gpa = typeof r.gpa === 'number' ? r.gpa.toFixed(2) : (r.gpa || 'N/A');
                const statusClass = r.status === 'PASSED' ? 'tag-passed' : 'tag-failed';
                let gradesHtml = '';
                if (r.grades && Object.keys(r.grades).length) {
                    gradesHtml = '<div class="grade-list">' +
                        Object.entries(r.grades).map(([s,g]) =>
                            `<div class="grade-item"><span>${s}</span><strong>${g}</strong></div>`
                        ).join('') + '</div>';
                }
                output.innerHTML += `
                    <div class="result-card">
                        <h4>Roll: ${res.roll}</h4>
                        <p><strong>Name:</strong> ${r.name || 'N/A'}</p>
                        <p><strong>Institute:</strong> ${r.school || 'N/A'}</p>
                        <p><strong>Group:</strong> ${r.group || 'N/A'}</p>
                        <p><strong>Status:</strong> <span class="${statusClass}">${r.status || 'N/A'}</span></p>
                        <p><strong>GPA:</strong> ${gpa} &nbsp; <strong>Total Marks:</strong> ${r.mark ?? 'N/A'}</p>
                        ${gradesHtml}
                    </div>`;
            } else {
                output.innerHTML += `
                    <div class="result-card">
                        <h4>Roll: ${res.roll}</h4>
                        <p class="msg-error">Failed: ${res.error}</p>
                    </div>`;
            }
        });
    } catch (err) {
        output.innerHTML = `<p class="msg-error">Request failed: ${err.message}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Fetch Results';
    }
});

// ─── ROLL FETCHER ───
document.getElementById('rolls-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eiin = document.getElementById('rolls-eiin-input').value.trim();
    if (!eiin) return;
    const btn = e.target.querySelector('button');
    const output = document.getElementById('rolls-output');
    btn.disabled = true;
    btn.textContent = 'Fetching...';
    output.innerHTML = '<p class="status-line">Fetching roll list(s)...</p>';

    try {
        const headers = {'Content-Type': 'application/json'};
        if (adminToken) headers['X-Admin-Token'] = adminToken;
        const resp = await fetch("${BACKEND_URL}"+"/api/rolls_fetch", {
            method: 'POST',
            headers,
            body: JSON.stringify({eiin, admin_token: adminToken})
        });
        const data = await resp.json();
        if (!data.ok) {
            output.innerHTML = `<p class="msg-error">${data.message}</p>`;
            return;
        }
        output.innerHTML = '';
        data.results.forEach(res => {
            if (res.ok) {
                output.innerHTML += `
                    <div class="summary" style="margin-bottom:12px">
                        <h3>${res.institute || 'EIIN ' + res.eiin} (EIIN: ${res.eiin})</h3>
                        <p><strong>District:</strong> ${res.district || 'N/A'} &nbsp;|&nbsp; <strong>Total Rolls:</strong> ${res.total}</p>
                        <div class="rolls-output" style="margin-top:8px">${res.rolls.join(' ')}</div>
                    </div>`;
            } else {
                output.innerHTML += `
                    <div class="summary" style="margin-bottom:12px;border-color:var(--red)">
                        <h3 style="color:var(--red)">EIIN: ${res.eiin}</h3>
                        <p class="msg-error">${res.message}</p>
                    </div>`;
            }
        });
    } catch (err) {
        output.innerHTML = `<p class="msg-error">Request failed: ${err.message}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Fetch Rolls';
    }
});

// ─── INSTITUTE SCRAPE (WebSocket) ───
document.getElementById('institute-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const eiin = document.getElementById('eiin-input').value.trim();
    if (!eiin) return;

    const progressArea = document.getElementById('institute-progress');
    const resultArea = document.getElementById('institute-results');
    const cancelBtn = document.getElementById('cancel-btn');
    const submitBtn = document.getElementById('institute-submit');

    progressArea.classList.add('visible');
    resultArea.innerHTML = '';
    submitBtn.disabled = true;
    cancelBtn.style.display = 'inline-flex';

    socket.emit('start_institute_scrape', {eiin, admin_token: adminToken});
});

document.getElementById('cancel-btn').addEventListener('click', () => {
    socket.emit('cancel_scrape', {job_id: currentJobId});
    document.getElementById('institute-status').textContent = '🛑 Scraping cancelled by user.';
    document.getElementById('cancel-btn').style.display = 'none';
    document.getElementById('institute-submit').disabled = false;
    currentJobId = null;
});

socket.on('scrape_started', (data) => {
    currentJobId = data.job_id;
    document.getElementById('institute-status').textContent = `Starting scrape for ${data.total_institutes} institute(s)...`;
});

socket.on('scrape_progress', (data) => {
    const bar = document.getElementById('progress-bar');
    const barText = document.getElementById('progress-text');
    const statusLine = document.getElementById('institute-status');

    const pct = data.total > 0 ? Math.round((data.done / data.total) * 100) : 0;
    bar.style.width = pct + '%';
    barText.textContent = data.total > 0 ? `${data.done}/${data.total} (${pct}%)` : '';

    let info = data.stage || '';
    if (data.success !== undefined) info += ` | OK: ${data.success} | Fail: ${data.failures}`;
    statusLine.textContent = info;
});

socket.on('institute_complete', (data) => {
    const resultArea = document.getElementById('institute-results');
    let downloadBtn = '';
    if (data.file_id) {
        downloadBtn = `<a class="btn btn-sm" href="/api/download/${data.file_id}" download="${data.filename}">Download JSON</a>`;
    }

    resultArea.innerHTML += `
        <div class="summary" style="margin-bottom:14px">
            <h3>✅ ${data.institute} (EIIN: ${data.eiin})</h3>
            <div class="summary-grid">
                <div class="summary-item"><span>District:</span> ${data.district || 'N/A'}</div>
                <div class="summary-item"><span>Total Students:</span> ${data.total}</div>
                <div class="summary-item"><span>Scraped:</span> ${data.scraped}</div>
                <div class="summary-item"><span>Passed:</span> ${data.passed}</div>
                <div class="summary-item"><span>Failed:</span> ${data.failed}</div>
                <div class="summary-item"><span>GPA 5.00:</span> ${data.gpa5}</div>
                <div class="summary-item"><span>Errors:</span> ${data.errors}</div>
                <div class="summary-item"><span>Time Taken:</span> ${data.elapsed || 'N/A'}</div>
            </div>
            <div style="margin-top:12px">${downloadBtn}</div>
        </div>`;
});

socket.on('institute_error', (data) => {
    const resultArea = document.getElementById('institute-results');
    resultArea.innerHTML += `
        <div class="summary" style="margin-bottom:14px;border-color:var(--red)">
            <h3 style="color:var(--red)">❌ EIIN: ${data.eiin}</h3>
            <p class="msg-error">${data.message}</p>
        </div>`;
});

socket.on('scrape_all_complete', (data) => {
    document.getElementById('cancel-btn').style.display = 'none';
    document.getElementById('institute-submit').disabled = false;
    document.getElementById('institute-status').textContent = data.message || 'All institutes processed.';
    currentJobId = null;

    if (data.zip_file_id) {
        const resultArea = document.getElementById('institute-results');
        const zipCard = `
            <div class="summary" style="margin-bottom:14px;border-color:var(--accent);background:linear-gradient(180deg, var(--surface), var(--bg))">
                <h3 style="color:var(--accent)">📦 Batch Scraping Completed (${data.total_files} institutes)</h3>
                <p style="margin: 8px 0;font-size:0.9rem">Download all institute result JSON files bundled into a single ZIP archive.</p>
                <a class="btn" style="background:#818cf8;color:#fff;margin-top:6px" href="/api/download/${data.zip_file_id}" download="${data.zip_filename}">⬇ Download All as ZIP (.zip)</a>
            </div>`;
        resultArea.insertAdjacentHTML('afterbegin', zipCard);
    }
});

socket.on('scrape_error', (data) => {
    document.getElementById('institute-results').innerHTML = `<p class="msg-error">${data.message}</p>`;
    document.getElementById('cancel-btn').style.display = 'none';
    document.getElementById('institute-submit').disabled = false;
    currentJobId = null;
});

socket.on('scrape_cancelled', (data) => {
    document.getElementById('institute-status').textContent = data.message || 'Cancelled.';
    document.getElementById('cancel-btn').style.display = 'none';
    document.getElementById('institute-submit').disabled = false;
    currentJobId = null;

    if (data.zip_file_id) {
        const resultArea = document.getElementById('institute-results');
        const zipCard = `
            <div class="summary" style="margin-bottom:14px;border-color:var(--yellow)">
                <h3 style="color:var(--yellow)">📦 Partial Results (${data.total_files} institutes completed before cancel)</h3>
                <a class="btn" style="background:var(--yellow);color:var(--bg);margin-top:6px" href="/api/download/${data.zip_file_id}" download="${data.zip_filename}">⬇ Download Completed JSONs as ZIP</a>
            </div>`;
        resultArea.insertAdjacentHTML('afterbegin', zipCard);
    }
});

// ─── COOKIE ───
async function loadCookie() {
    const resp = await fetch("${BACKEND_URL}"+"/api/cookie");
    const data = await resp.json();
    document.getElementById('cookie-current').textContent = data.cookie || 'No cookie saved.';
}

document.getElementById('cookie-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const cookie = document.getElementById('cookie-input').value.trim();
    if (!cookie) return;
    const resp = await fetch("${BACKEND_URL}"+"/api/cookie", {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({cookie})
    });
    const data = await resp.json();
    document.getElementById('cookie-msg').textContent = data.message;
    document.getElementById('cookie-input').value = '';
    loadCookie();
});

// Initial load
checkAuthStatus();
loadCookie();
