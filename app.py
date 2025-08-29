
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, send_from_directory
import uuid
from datetime import datetime
import secrets
import base64
import os

app = Flask(__name__, static_folder='static')
app.secret_key = secrets.token_hex(16)

balances = {}  # {username: balance}
pending_approvals = []  # List of pending dicts

# Hardcoded admin credentials
ADMIN_USERNAME = 'JIMMY'
ADMIN_PASSWORD = 'JIMMY1'

# Hardcoded UPI ID
UPI_ID = 'yourupi@bank'  # Replace with actual UPI ID

# Path to QR code (if needed, but removed per requirements)
QR_PATH = 'QR.png'

# Serve static files (e.g., scan862.gif)
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
            return redirect(url_for('admin_panel'))
        else:
            return render_template_string(LOGIN_HTML, error='Invalid credentials')
    return render_template_string(LOGIN_HTML, error=None)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/get_balance', methods=['POST'])
def get_balance():
    data = request.get_json()
    username = data.get('UserName')
    if username not in balances:
        balances[username] = 0
    return jsonify({"success": True, "balance": balances[username]})

@app.route('/update_balance', methods=['POST'])
def update_balance():
    data = request.get_json()
    username = data.get('UserName')
    new_balance = data.get('balance')
    if username is None or new_balance is None:
        return jsonify({"success": False, "message": "Invalid data"}), 400
    if new_balance < 0:
        return jsonify({"success": False, "message": "Balance cannot be negative"}), 400
    balances[username] = new_balance
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
    pending_approvals.append({
        'id': pending_id,
        'license_key': license_key,
        'utr': utr,
        'requested_amount': amount,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'screenshot_base64': screenshot_base64
    })
    return jsonify({"success": True, "message": "Pending"})

@app.route('/get_upi', methods=['GET'])
def get_upi():
    return jsonify({"success": True, "upi_id": UPI_ID})

@app.route('/admin')
def admin_panel():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template_string(ADMIN_HTML, pendings=pending_approvals)

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
    for pending in pending_approvals[:]:
        if pending['id'] == pending_id:
            username = pending['license_key']
            if username not in balances:
                balances[username] = 0
            balances[username] += amount
            pending_approvals.remove(pending)
            break
    return redirect(url_for('admin_panel'))

@app.route('/reject/<pending_id>', methods=['POST'])
def reject(pending_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    for pending in pending_approvals[:]:
        if pending['id'] == pending_id:
            pending_approvals.remove(pending)
            break
    return redirect(url_for('admin_panel'))

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

ADMIN_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
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
            <a href="/logout" class="btn btn-secondary">Logout</a>
        </div>
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
        {% if not pendings %}
        <p>No pending approvals.</p>
        {% endif %}
    </div>
</body>
</html>
'''

# if __name__ == '__main__':
#     # Create static directory if it doesn't exist
#     if not os.path.exists('static'):
#         os.makedirs('static')
#     app.run(host='0.0.0.0', port=8000, debug=True)
