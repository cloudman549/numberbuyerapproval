from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, send_from_directory
import uuid
from datetime import datetime, timedelta
import secrets
import base64
import os
from pymongo import MongoClient
from bson import ObjectId

app = Flask(__name__, static_folder='static')
app.secret_key = secrets.token_hex(16)

# MongoDB connection
client = MongoClient('mongodb+srv://Email_database:Cloudman%40100@approve1.94eexy7.mongodb.net/?retryWrites=true&w=majority&appName=Approve1')
db = client['Approve1']
licenses_col = db['licenses']
pending_approvals_col = db['pending_approvals']
emails_col = db['emails']
api_keys_col = db['api_keys']

# Initialize collections if they don't exist
if 'licenses' not in db.list_collection_names():
    db.create_collection('licenses')
if 'pending_approvals' not in db.list_collection_names():
    db.create_collection('pending_approvals')
if 'emails' not in db.list_collection_names():
    db.create_collection('emails')
if 'api_keys' not in db.list_collection_names():
    db.create_collection('api_keys')

# Hardcoded admin credentials
ADMIN_USERNAME = 'JIMMY'
ADMIN_PASSWORD = 'JIMMY1'

# Path to QR code (if needed, but removed per requirements)
QR_PATH = 'QR.png'

# Serve static files
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            return render_template_string(LOGIN_HTML, error='Invalid credentials')
    return render_template_string(LOGIN_HTML, error=None)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    # Calculate statistics
    approved_pendings = list(pending_approvals_col.find({'status': 'approved'}))
    total_approved = sum(pending.get('approved_amount', pending.get('requested_amount', 0)) for pending in approved_pendings)
    total_licenses = licenses_col.count_documents({})
    total_emails_sold = emails_col.count_documents({'sold': True})
    total_emails_available = emails_col.count_documents({'sold': False})
    total_pending = pending_approvals_col.count_documents({'status': 'pending'})
    message = request.args.get('message')
    return render_template_string(DASHBOARD_HTML, 
        total_approved=total_approved,
        total_licenses=total_licenses,
        total_emails_sold=total_emails_sold,
        total_emails_available=total_emails_available,
        total_pending=total_pending,
        message=message
    )

@app.route('/get_balance', methods=['POST'])
def get_balance():
    data = request.get_json()
    username = data.get('UserName')
    license = licenses_col.find_one({"key": username})
    balance = license.get('balance', 0) if license else 0
    return jsonify({"success": True, "balance": balance})

@app.route('/update_balance', methods=['POST'])
def update_balance():
    data = request.get_json()
    username = data.get('UserName')
    new_balance = data.get('balance')
    if username is None or new_balance is None:
        return jsonify({"success": False, "message": "Invalid data"}), 400
    if new_balance < 0:
        return jsonify({"success": False, "message": "Balance cannot be negative"}), 400
    licenses_col.update_one({"key": username}, {"$set": {"balance": new_balance}}, upsert=True)
    return jsonify({"success": True})

@app.route('/submit_utr', methods=['POST'])
def submit_utr():
    license_key = request.form.get('license_key')
    utr = request.form.get('utr')
    amount = request.form.get('amount')
    screenshot = request.files.get('screenshot')
    if not license_key or not utr or not amount or not screenshot:
        return jsonify({"success": False, "message": "Missing required fields or screenshot"}), 400
    try:
        amount = int(amount)
        if amount <= 0:
            return jsonify({"success": False, "message": "Amount must be positive"}), 400
    except ValueError:
        return jsonify({"success": False, "message": "Invalid amount"}), 400
    screenshot_base64 = base64.b64encode(screenshot.read()).decode('utf-8')
    pending_id = str(uuid.uuid4())
    pending_approvals_col.insert_one({
        'id': pending_id,
        'license_key': license_key,
        'utr': utr,
        'requested_amount': amount,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'screenshot_base64': screenshot_base64,
        'status': 'pending'
    })
    return jsonify({"success": True, "message": "Pending"})

@app.route('/get_upi', methods=['GET'])
def get_upi():
    apis = api_keys_col.find_one({"_id": "api_config"}) or {}
    upi_id = apis.get('upi_id', 'yourupi@bank')
    return jsonify({"success": True, "upi_id": upi_id})

@app.route('/validate_license', methods=['POST'])
def validate_license():
    data = request.get_json()
    license_key = data.get('UserName')
    mac_address = data.get('MacAddress')
    lic = licenses_col.find_one({"key": license_key})
    if not lic:
        return jsonify({"success": False, "message": "License key not found"}), 404
    if not lic["active"]:
        return jsonify({"success": False, "message": "License is deactivated"}), 400
    if not lic["paid"]:
        return jsonify({"success": False, "message": "License is unpaid"}), 400
    expiry_date = datetime.strptime(lic["expiry"], '%Y-%m-%d')
    days_left = (expiry_date - datetime.now()).days
    if days_left < 0:
        return jsonify({"success": False, "message": "License expired"}), 400
    if lic["mac"] == "" or lic["mac"] == mac_address:
        licenses_col.update_one({"key": license_key}, {"$set": {"mac": mac_address}})
        return jsonify({"success": True, "leftDays": days_left, "plan": lic.get("plan", "Basic")}), 200
    return jsonify({"success": False, "message": "License bound to another device"}), 400

@app.route('/create_license', methods=['POST'])
def create_license():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    requested_key = request.form.get('license_key', '').strip().upper()
    if not requested_key or licenses_col.find_one({"key": requested_key}):
        return redirect(url_for('dashboard', message='License key already exists or empty.'))
    expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    licenses_col.insert_one({
        "key": requested_key,
        "seller": ADMIN_USERNAME,
        "mac": "",
        "expiry": expiry,
        "active": True,
        "plan": "Basic",
        "paid": True,
        "created_at": datetime.now(),
        "balance": 0
    })
    return redirect(url_for('dashboard', message='License created successfully.'))

@app.route('/upload_email', methods=['POST'])
def upload_email():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    file = request.files.get('file')
    if not file:
        return redirect(url_for('dashboard', message='No file uploaded.'))
    try:
        content = file.read().decode('utf-8')
        blocks = content.split('--------------------------------------------------')
        count = 0
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            lines = block.split('\n')
            email = None
            password = None
            for line in lines:
                if line.startswith('Email:'):
                    email = line.split(':', 1)[1].strip()
                elif line.startswith('Password:'):
                    password = line.split(':', 1)[1].strip()
            if email and password and not emails_col.find_one({"email": email}):
                emails_col.insert_one({
                    "email": email,
                    "password": password,
                    "sold": False,
                    "created_at": datetime.now()
                })
                count += 1
        return redirect(url_for('dashboard', message=f'{count} emails uploaded successfully.'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error uploading file: {str(e)}'))

@app.route('/get_email', methods=['GET'])
def get_email():
    email_doc = emails_col.find_one_and_update({"sold": False}, {"$set": {"sold": True}})
    if email_doc:
        return jsonify({"success": True, "email": email_doc["email"], "password": email_doc["password"]})
    else:
        return jsonify({"success": False, "message": "No available emails.contact admin"}), 404

@app.route('/pending')
def pending_approvals():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    pendings = list(pending_approvals_col.find({'status': 'pending'}))
    message = request.args.get('message')
    return render_template_string(PENDING_HTML, pendings=pendings, message=message)

@app.route('/approve/<pending_id>', methods=['POST'])
def approve(pending_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    amount = request.form.get('amount')
    try:
        amount = int(amount)
        if amount <= 0:
            return "Amount must be positive", 400
    except ValueError:
        return "Invalid amount", 400
    pending = pending_approvals_col.find_one({'id': pending_id})
    if pending:
        username = pending['license_key']
        licenses_col.update_one(
            {"key": username},
            {"$inc": {"balance": amount}, "$set": {"paid": True}},
            upsert=True
        )
        pending_approvals_col.update_one(
            {'id': pending_id},
            {"$set": {"status": "approved", "approved_amount": amount}}
        )
    return redirect(url_for('pending_approvals', message='Approval successful'))

@app.route('/reject/<pending_id>', methods=['POST'])
def reject(pending_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    pending_approvals_col.update_one(
        {'id': pending_id},
        {"$set": {"status": "rejected"}}
    )
    return redirect(url_for('pending_approvals', message='Rejection successful'))

@app.route('/approved')
def approved():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    approveds = list(pending_approvals_col.find({'status': 'approved'}))
    message = request.args.get('message')
    return render_template_string(APPROVED_HTML, approveds=approveds, message=message)

@app.route('/delete_approved/<pending_id>', methods=['POST'])
def delete_approved(pending_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    pending = pending_approvals_col.find_one({'id': pending_id, 'status': 'approved'})
    if pending:
        username = pending['license_key']
        amount = pending.get('approved_amount', pending.get('requested_amount', 0))
        licenses_col.update_one(
            {"key": username},
            {"$inc": {"balance": -amount}}
        )
        pending_approvals_col.delete_one({'id': pending_id})
    return redirect(url_for('approved', message='Approved transaction deleted successfully'))

@app.route('/licenses')
def licenses():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    licenses_list = list(licenses_col.find())
    message = request.args.get('message')
    return render_template_string(LICENSES_HTML, licenses=licenses_list, message=message)

@app.route('/delete_license/<key>', methods=['POST'])
def delete_license(key):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    licenses_col.delete_one({"key": key})
    return redirect(url_for('licenses', message='License deleted successfully'))

@app.route('/reset_license/<key>', methods=['POST'])
def reset_license(key):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    licenses_col.update_one({"key": key}, {"$set": {"mac": ""}})
    return redirect(url_for('licenses', message='License reset successfully'))

@app.route('/emails_sold')
def emails_sold():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    sold = list(emails_col.find({'sold': True}))
    message = request.args.get('message')
    return render_template_string(EMAILS_SOLD_HTML, emails=sold, message=message)

@app.route('/emails_available')
def emails_available():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    available = list(emails_col.find({'sold': False}))
    message = request.args.get('message')
    return render_template_string(EMAILS_AVAILABLE_HTML, emails=available, message=message)

@app.route('/delete_email/<email_id>', methods=['POST'])
def delete_email(email_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    emails_col.delete_one({"_id": ObjectId(email_id)})
    prev = request.referrer or url_for('dashboard')
    message = 'Email deleted successfully'
    if 'emails_sold' in prev:
        return redirect(url_for('emails_sold', message=message))
    elif 'emails_available' in prev:
        return redirect(url_for('emails_available', message=message))
    else:
        return redirect(url_for('dashboard', message=message))

@app.route('/mark_sold/<email_id>', methods=['POST'])
def mark_sold(email_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    emails_col.update_one({"_id": ObjectId(email_id)}, {"$set": {"sold": True}})
    return redirect(url_for('emails_available', message='Email marked as sold successfully'))

@app.route('/mark_unsold/<email_id>', methods=['POST'])
def mark_unsold(email_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    emails_col.update_one({"_id": ObjectId(email_id)}, {"$set": {"sold": False}})
    return redirect(url_for('emails_sold', message='Email marked as unsold successfully'))

@app.route('/update_api', methods=['GET', 'POST'])
def update_api():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        otpbuy = request.form.get('otpbuy')
        grizzlysms = request.form.get('grizzlysms')
        api_keys_col.update_one({"_id": "api_config"}, {"$set": {"otpbuy": otpbuy, "grizzlysms": grizzlysms}}, upsert=True)
        return redirect(url_for('dashboard', message='API keys updated successfully.'))
    apis = api_keys_col.find_one({"_id": "api_config"}) or {}
    otpbuy = apis.get('otpbuy', '')
    grizzlysms = apis.get('grizzlysms', '')
    return render_template_string(UPDATE_API_HTML, otpbuy=otpbuy, grizzlysms=grizzlysms)

@app.route('/update_upi', methods=['GET', 'POST'])
def update_upi():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        upi_id = request.form.get('upi_id')
        api_keys_col.update_one({"_id": "api_config"}, {"$set": {"upi_id": upi_id}}, upsert=True)
        return redirect(url_for('dashboard', message='UPI ID updated successfully.'))
    apis = api_keys_col.find_one({"_id": "api_config"}) or {}
    upi_id = apis.get('upi_id', '')
    return render_template_string(UPDATE_UPI_HTML, upi_id=upi_id)

@app.route('/get_api', methods=['GET'])
def get_api():
    apis = api_keys_col.find_one({"_id": "api_config"}) or {}
    otpbuy = apis.get('otpbuy', '')
    grizzlysms = apis.get('grizzlysms', '')
    api_string = f"otpbuy.org:-{otpbuy} ,grizzlysms:- {grizzlysms}"
    return jsonify({"success": True, "api_keys": api_string})

LOGIN_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8f9fa; }
        .login-container { max-width: 400px; margin: auto; padding: 20px; background: white; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
    </style>
</head>
<body>
    <div class="container mt-5">
        <div class="login-container">
            <h2 class="text-center mb-4">Admin Login</h2>
            {% if error %}
            <div class="alert alert-danger">{{ error }}</div>
            {% endif %}
            <form method="post">
                <div class="mb-3">
                    <label for="username" class="form-label">Username</label>
                    <input type="text" class="form-control" id="username" name="username" required>
                </div>
                <div class="mb-3">
                    <label for="password" class="form-label">Password</label>
                    <input type="password" class="form-control" id="password" name="password" required>
                </div>
                <button type="submit" class="btn btn-primary w-100">Login</button>
            </form>
        </div>
    </div>
</body>
</html>
'''

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        .sidebar { height: 100vh; width: 0; position: fixed; z-index: 1000; top: 0; left: 0; background-color: #222; overflow-x: hidden; transition: 0.5s; padding-top: 60px; }
        .sidebar a { padding: 8px 8px 8px 32px; text-decoration: none; font-size: 20px; color: lime; display: block; transition: 0.3s; }
        .sidebar a:hover { color: #f1f1f1; }
        .sidebar .closebtn { position: absolute; top: 0; right: 25px; font-size: 36px; margin-left: 50px; }
        .menu-icon { font-size: 30px; cursor: pointer; }
        .stats-card { background: #1a1a1a; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        .stats-card a { color: lime; text-decoration: none; }
        .main-content { transition: margin-left .5s; padding: 16px; }
        @media screen and (max-height: 450px) { .sidebar {padding-top: 15px;} .sidebar a {font-size: 18px;} }
    </style>
    <script>
        function openNav() { document.getElementById("mySidebar").style.width = "250px"; document.getElementById("main").style.marginLeft = "250px"; }
        function closeNav() { document.getElementById("mySidebar").style.width = "0"; document.getElementById("main").style.marginLeft= "0"; }
    </script>
</head>
<body>
    <div id="mySidebar" class="sidebar">
        <a href="javascript:void(0)" class="closebtn" onclick="closeNav()">&times;</a>
        <a href="#createLicense" onclick="document.getElementById('createLicenseForm').style.display='block'; closeNav();"><i class="bi bi-plus-circle-fill"></i> Create Key</a>
        <a href="#uploadEmail" onclick="document.getElementById('uploadEmailForm').style.display='block'; closeNav();"><i class="bi bi-envelope-plus-fill"></i> Upload Email</a>
        <a href="/pending"><i class="bi bi-clock-fill"></i> Pending Approvals</a>
        <a href="/update_api"><i class="bi bi-gear-fill"></i> Update API</a>
        <a href="/update_upi"><i class="bi bi-credit-card-fill"></i> Update UPI ID</a>
        <a href="/logout"><i class="bi bi-box-arrow-right"></i> Logout</a>
    </div>
    <div id="main" class="main-content">
        <span class="menu-icon" onclick="openNav()">&#9776;</span>
        <h1>Admin Dashboard</h1>
        {% if message %}
        <div class="alert alert-info">{{ message }}</div>
        {% endif %}
        <div class="row">
            <div class="col-md-4">
                <div class="stats-card">
                    <a href="/approved">
                        <h3><i class="bi bi-check-circle-fill"></i> Total Amount Approved</h3>
                        <p>{{ total_approved }}</p>
                    </a>
                </div>
            </div>
            <div class="col-md-4">
                <div class="stats-card">
                    <a href="/licenses">
                        <h3><i class="bi bi-key-fill"></i> Total Licenses Created</h3>
                        <p>{{ total_licenses }}</p>
                    </a>
                </div>
            </div>
            <div class="col-md-4">
                <div class="stats-card">
                    <a href="/emails_sold">
                        <h3><i class="bi bi-envelope-check-fill"></i> Total Emails Sold</h3>
                        <p>{{ total_emails_sold }}</p>
                    </a>
                </div>
            </div>
            <div class="col-md-4">
                <div class="stats-card">
                    <a href="/emails_available">
                        <h3><i class="bi bi-envelope-fill"></i> Emails Available</h3>
                        <p>{{ total_emails_available }}</p>
                    </a>
                </div>
            </div>
            <div class="col-md-4">
                <div class="stats-card">
                    <a href="/pending">
                        <h3><i class="bi bi-hourglass-split"></i> Pending Approvals</h3>
                        <p>{{ total_pending }}</p>
                    </a>
                </div>
            </div>
        </div>
        <div id="createLicenseForm" style="display:none;" class="stats-card">
            <h3>Create License</h3>
            <form action="/create_license" method="post">
                <div class="mb-3">
                    <label for="license_key" class="form-label">License Key</label>
                    <input type="text" class="form-control" id="license_key" name="license_key" required>
                </div>
                <button type="submit" class="btn btn-primary">Create</button>
            </form>
        </div>
        <div id="uploadEmailForm" style="display:none;" class="stats-card">
            <h3>Upload Emails TXT File</h3>
            <form action="/upload_email" method="post" enctype="multipart/form-data">
                <div class="mb-3">
                    <label for="file" class="form-label">TXT File</label>
                    <input type="file" class="form-control" id="file" name="file" required accept=".txt">
                </div>
                <button type="submit" class="btn btn-primary">Upload</button>
            </form>
        </div>
    </div>
</body>
</html>
'''

PENDING_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pending Approvals</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; }
        th, td { border: 1px solid #333; padding: 12px; text-align: left; }
        th { background: #222; }
        .btn { margin-left: 5px; }
    </style>
    <script>
        function enlargeImage(img) {
            var modal = document.createElement('div');
            modal.style.position = 'fixed';
            modal.style.zIndex = '1000';
            modal.style.left = '0';
            modal.style.top = '0';
            modal.style.width = '100%';
            modal.style.height = '100%';
            modal.style.backgroundColor = 'rgba(0,0,0,0.8)';
            var modalImg = document.createElement('img');
            modalImg.src = img.src;
            modalImg.style.margin = 'auto';
            modalImg.style.display = 'block';
            modalImg.style.maxWidth = '80%';
            modalImg.style.maxHeight = '80%';
            modal.style.display = 'flex';
            modal.style.alignItems = 'center';
            modal.style.justifyContent = 'center';
            modal.appendChild(modalImg);
            document.body.appendChild(modal);
            modal.onclick = function() {
                document.body.removeChild(modal);
            }
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Pending Approvals</h1>
            <a href="/dashboard" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        {% if message %}
        <div class="alert alert-info">{{ message }}</div>
        {% endif %}
        <table>
            <tr><th>ID</th><th>License Key</th><th>UTR</th><th>Amount</th><th>Timestamp</th><th>Screenshot</th><th>Actions</th></tr>
            {% for pending in pendings %}
            <tr>
                <td>{{ pending['id'] }}</td>
                <td>{{ pending['license_key'] }}</td>
                <td>{{ pending['utr'] }}</td>
                <td>{{ pending['requested_amount'] }}</td>
                <td>{{ pending['timestamp'] }}</td>
                <td><img src="data:image/jpeg;base64,{{ pending['screenshot_base64'] }}" alt="Screenshot" style="max-width: 100px; cursor: pointer;" onclick="enlargeImage(this)"></td>
                <td>
                    <form action="/approve/{{ pending['id'] }}" method="post" class="d-inline">
                        <input type="number" name="amount" value="{{ pending['requested_amount'] }}" style="width: 60px;">
                        <button type="submit" class="btn btn-success">Approve</button>
                    </form>
                    <form action="/reject/{{ pending['id'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-danger">Reject</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% if not pendings|length %}
        <p>No pending approvals.</p>
        {% endif %}
    </div>
</body>
</html>
'''

APPROVED_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Approved Transactions</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; }
        th, td { border: 1px solid #333; padding: 12px; text-align: left; }
        th { background: #222; }
        .btn { margin-left: 5px; }
    </style>
    <script>
        function enlargeImage(img) {
            var modal = document.createElement('div');
            modal.style.position = 'fixed';
            modal.style.zIndex = '1000';
            modal.style.left = '0';
            modal.style.top = '0';
            modal.style.width = '100%';
            modal.style.height = '100%';
            modal.style.backgroundColor = 'rgba(0,0,0,0.8)';
            var modalImg = document.createElement('img');
            modalImg.src = img.src;
            modalImg.style.margin = 'auto';
            modalImg.style.display = 'block';
            modalImg.style.maxWidth = '80%';
            modalImg.style.maxHeight = '80%';
            modal.style.display = 'flex';
            modal.style.alignItems = 'center';
            modal.style.justifyContent = 'center';
            modal.appendChild(modalImg);
            document.body.appendChild(modal);
            modal.onclick = function() {
                document.body.removeChild(modal);
            }
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Approved Transactions</h1>
            <a href="/dashboard" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        {% if message %}
        <div class="alert alert-info">{{ message }}</div>
        {% endif %}
        <table>
            <tr><th>ID</th><th>License Key</th><th>UTR</th><th>Approved Amount</th><th>Timestamp</th><th>Screenshot</th><th>Actions</th></tr>
            {% for approved in approveds %}
            <tr>
                <td>{{ approved['id'] }}</td>
                <td>{{ approved['license_key'] }}</td>
                <td>{{ approved['utr'] }}</td>
                <td>{{ approved.get('approved_amount', approved['requested_amount']) }}</td>
                <td>{{ approved['timestamp'] }}</td>
                <td><img src="data:image/jpeg;base64,{{ approved['screenshot_base64'] }}" alt="Screenshot" style="max-width: 100px; cursor: pointer;" onclick="enlargeImage(this)"></td>
                <td>
                    <form action="/delete_approved/{{ approved['id'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-danger">Delete</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% if not approveds|length %}
        <p>No approved transactions.</p>
        {% endif %}
    </div>
</body>
</html>
'''

LICENSES_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Licenses</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; }
        th, td { border: 1px solid #333; padding: 12px; text-align: left; }
        th { background: #222; }
        .btn { margin-left: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Licenses</h1>
            <a href="/dashboard" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        {% if message %}
        <div class="alert alert-info">{{ message }}</div>
        {% endif %}
        <table>
            <tr><th>Key</th><th>Seller</th><th>MAC</th><th>Expiry</th><th>Active</th><th>Plan</th><th>Paid</th><th>Created At</th><th>Balance</th><th>Actions</th></tr>
            {% for license in licenses %}
            <tr>
                <td>{{ license['key'] }}</td>
                <td>{{ license['seller'] }}</td>
                <td>{{ license['mac'] }}</td>
                <td>{{ license['expiry'] }}</td>
                <td>{{ license['active'] }}</td>
                <td>{{ license['plan'] }}</td>
                <td>{{ license['paid'] }}</td>
                <td>{{ license['created_at'] }}</td>
                <td>{{ license['balance'] }}</td>
                <td>
                    <form action="/reset_license/{{ license['key'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-warning">Reset</button>
                    </form>
                    <form action="/delete_license/{{ license['key'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-danger">Delete</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% if not licenses|length %}
        <p>No licenses.</p>
        {% endif %}
    </div>
</body>
</html>
'''

EMAILS_SOLD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sold Emails</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; }
        th, td { border: 1px solid #333; padding: 12px; text-align: left; }
        th { background: #222; }
        .btn { margin-left: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Sold Emails</h1>
            <a href="/dashboard" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        {% if message %}
        <div class="alert alert-info">{{ message }}</div>
        {% endif %}
        <table>
            <tr><th>Email</th><th>Password</th><th>Created At</th><th>Actions</th></tr>
            {% for email in emails %}
            <tr>
                <td>{{ email['email'] }}</td>
                <td>{{ email['password'] }}</td>
                <td>{{ email['created_at'] }}</td>
                <td>
                    <form action="/mark_unsold/{{ email['_id'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-warning">Mark Unsold</button>
                    </form>
                    <form action="/delete_email/{{ email['_id'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-danger">Delete</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% if not emails|length %}
        <p>No sold emails.</p>
        {% endif %}
    </div>
</body>
</html>
'''

EMAILS_AVAILABLE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Available Emails</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; }
        th, td { border: 1px solid #333; padding: 12px; text-align: left; }
        th { background: #222; }
        .btn { margin-left: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Available Emails</h1>
            <a href="/dashboard" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        {% if message %}
        <div class="alert alert-info">{{ message }}</div>
        {% endif %}
        <table>
            <tr><th>Email</th><th>Password</th><th>Created At</th><th>Actions</th></tr>
            {% for email in emails %}
            <tr>
                <td>{{ email['email'] }}</td>
                <td>{{ email['password'] }}</td>
                <td>{{ email['created_at'] }}</td>
                <td>
                    <form action="/mark_sold/{{ email['_id'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-success">Mark Sold</button>
                    </form>
                    <form action="/delete_email/{{ email['_id'] }}" method="post" class="d-inline">
                        <button type="submit" class="btn btn-danger">Delete</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% if not emails|length %}
        <p>No available emails.</p>
        {% endif %}
    </div>
</body>
</html>
'''

UPDATE_API_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Update API Keys</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        .container { max-width: 600px; }
        .form-control { background-color: #1a1a1a; color: lime; border: 1px solid #333; }
        .form-control:focus { background-color: #1a1a1a; color: lime; border-color: lime; box-shadow: none; }
        .btn-primary { background-color: #28a745; border-color: #28a745; }
        .btn-primary:hover { background-color: #218838; border-color: #1e7e34; }
        .btn-secondary { background-color: #6c757d; border-color: #6c757d; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Update API Keys</h1>
            <a href="/dashboard" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        <form method="post">
            <div class="mb-3">
                <label for="otpbuy" class="form-label">OTPBbuy.org API Key</label>
                <input type="text" class="form-control" id="otpbuy" name="otpbuy" value="{{ otpbuy }}" required>
            </div>
            <div class="mb-3">
                <label for="grizzlysms" class="form-label">GrizzlySMS API Key</label>
                <input type="text" class="form-control" id="grizzlysms" name="grizzlysms" value="{{ grizzlysms }}" required>
            </div>
            <button type="submit" class="btn btn-primary">Update</button>
        </form>
    </div>
</body>
</html>
'''

UPDATE_UPI_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Update UPI ID</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body { background-color: #0d0d0d; color: lime; padding: 20px; }
        .container { max-width: 600px; }
        .form-control { background-color: #1a1a1a; color: lime; border: 1px solid #333; }
        .form-control:focus { background-color: #1a1a1a; color: lime; border-color: lime; box-shadow: none; }
        .btn-primary { background-color: #28a745; border-color: #28a745; }
        .btn-primary:hover { background-color: #218838; border-color: #1e7e34; }
        .btn-secondary { background-color: #6c757d; border-color: #6c757d; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Update UPI ID</h1>
            <a href="/dashboard" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        <form method="post">
            <div class="mb-3">
                <label for="upi_id" class="form-label">UPI ID</label>
                <input type="text" class="form-control" id="upi_id" name="upi_id" value="{{ upi_id }}" required>
            </div>
            <button type="submit" class="btn btn-primary">Update</button>
        </form>
    </div>
</body>
</html>
'''

if __name__ == '__main__':
    # Create static directory if it doesn't exist
    if not os.path.exists('static'):
        os.makedirs('static')
    app.run(host='0.0.0.0', port=5001, debug=True)