from flask import Flask, render_template, request, session, redirect, url_for, jsonify, Response, send_file, send_from_directory
import ast
import string
import os
import io
import csv
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import requests
import json
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

SAST = timezone(timedelta(hours=2))

def format_timestamp(ts):
    if not ts:
        return ""
    if hasattr(ts, 'astimezone'):
        return ts.astimezone(SAST).strftime('%d %b %Y, %H:%M')
    return str(ts)[:16]

import firebase_admin
from firebase_admin import credentials, firestore


# Optional ReportLab if installed
try:
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

env_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    print(f"[SYSTEM DEBUG] Loaded SMTP_EMAIL: {os.getenv('SMTP_EMAIL')}")

# ---------------- FIREBASE INITIALIZATION ----------------
try:
    # Look for service account key in project root
    cred_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'serviceAccountKey.json')
    firebase_creds_json = os.getenv('FIREBASE_CREDENTIALS_JSON')
    if firebase_creds_json:
        import json
        print(f"[SYSTEM DEBUG] Found FIREBASE_CREDENTIALS_JSON env var. Length: {len(firebase_creds_json)}")
        try:
            cred_dict = json.loads(firebase_creds_json)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {
                'storageBucket': f"{cred_dict.get('project_id', 'smarties-ticket-system')}.appspot.com"
            })
            print("[SYSTEM DEBUG] Initialized using FIREBASE_CREDENTIALS_JSON")
        except json.JSONDecodeError as je:
            print(f"[SYSTEM DEBUG] JSON Decode Error: {je}. First 50 chars: {repr(firebase_creds_json[:50])}")
            raise
    elif os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred, {
            'storageBucket': 'smarties-ticket-system.appspot.com'
        })
        print("[SYSTEM DEBUG] Initialized using serviceAccountKey.json")
    else:
        # Fallback to default credentials if running in GCP
        firebase_admin.initialize_app(None, {
            'storageBucket': 'smarties-ticket-system.appspot.com'
        })
        print("[SYSTEM DEBUG] Initialized using default GCP credentials")
    
    db = firestore.client(database_id='default')
    print("[SYSTEM DEBUG] Firestore Initialized Successfully")
except Exception as e:
    print(f"[SYSTEM DEBUG] Firestore Initialization Error: {e}")
    db = None

# ReportLab for PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.legends import Legend

# ---------------- KEYWORDS ----------------
IT_KEYWORDS = ["password","computer","wifi","laptop","printer","email","system","software","network","login","internet","server","bug","error","update","pc"]
FINANCE_KEYWORDS = ["salary","payment","payslip","invoice","refund","budget","expense","tax","bank","billing"]
HR_KEYWORDS = ["leave","holiday","vacation","sick","promotion","training","resignation","contract","benefits"]
OPERATIONS_KEYWORDS = ["office","chair","desk","aircon","maintenance","cleaning","electricity","water","parking","security"]

URGENT_WORDS = ["urgent","asap","immediately","now","critical","system down","not working","failed","error"]
FRIENDLY_WORDS = ["hi","hello","please","could you","thank you","kindly"]

DEPARTMENTS = ["IT", "Finance", "HR", "Operations"]

# ---------------- APP ----------------
app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# ---------------- UPLOADS ----------------
UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'txt', 'log', 'mp4', 'mov', 'avi', 'mkv', 'doc', 'docx', 'xls', 'xlsx'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS



def init_db():
    """
    Seed initial data into Firestore if collections are empty.
    """
    if db is None:
        print("[SYSTEM DEBUG] Firestore not initialized. Skipping seed.")
        return

    # Seed default automation rules
    print("[SYSTEM DEBUG] Checking automation_rules...")
    rules_ref = db.collection('automation_rules')
    if len(list(rules_ref.limit(1).stream())) == 0:
        print("[SYSTEM DEBUG] Seeding automation_rules...")
        rules = [
            {'keyword': 'salary', 'target_department': 'Finance', 'target_priority': 'High', 'is_active': 1},
            {'keyword': 'urgent', 'target_department': 'IT', 'target_priority': 'Emergency', 'is_active': 1},
            {'keyword': 'leave', 'target_department': 'HR', 'target_priority': 'Normal', 'is_active': 1},
            {'keyword': 'invoice', 'target_department': 'Finance', 'target_priority': 'Normal', 'is_active': 1},
            {'keyword': 'water', 'target_department': 'Operations', 'target_priority': 'Normal', 'is_active': 1}
        ]
        for rule in rules:
            rules_ref.add(rule)
        print("[SYSTEM DEBUG] Seeded automation_rules")
    else:
        print("[SYSTEM DEBUG] automation_rules already exist")

    # Seed default Administrator
    print("[SYSTEM DEBUG] Checking Administrator user...")
    users_ref = db.collection('users')
    admin_query = users_ref.where('username', '==', 'Administrator').limit(1).stream()
    if len(list(admin_query)) == 0:
        print("[SYSTEM DEBUG] Seeding Administrator user...")
        admin_pw = generate_password_hash("Admin123")
        users_ref.add({
            'username': 'Administrator',
            'email': 'admin@smarties.com',
            'password': admin_pw,
            'role': 'admin',
            'department': 'IT',
            'created_at': firestore.SERVER_TIMESTAMP
        })
        print("[SYSTEM DEBUG] Seeded Administrator user")
    else:
        print("[SYSTEM DEBUG] Administrator user already exists")

    # Seed counters
    counters_ref = db.collection('counters')
    if not counters_ref.document('tickets').get().exists:
        counters_ref.document('tickets').set({'last_id': 1000})
        print("[SYSTEM DEBUG] Seeded ticket counter")

init_db()

def get_next_id(counter_name):
    """Atomically increment and return the next ID for a collection."""
    counter_ref = db.collection('counters').document(counter_name)
    
    @firestore.transactional
    def update_counter(transaction, ref):
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            new_id = 1001
        else:
            new_id = snapshot.get('last_id') + 1
        transaction.update(ref, {'last_id': new_id})
        return new_id

    transaction = db.transaction()
    return update_counter(transaction, counter_ref)

# ---------------- AUDIT ----------------
def log_action(action, table_name, row_id, old_value, new_value, performer=None):
    try:
        if db is None: return
        performed_by = performer if performer else session.get('username', 'Unknown')
        db.collection('audit_log').add({
            'action': action,
            'table_name': table_name,
            'row_id': str(row_id),
            'old_value': str(old_value),
            'new_value': str(new_value),
            'timestamp': firestore.SERVER_TIMESTAMP,
            'performer': performed_by
        })
    except Exception as e:
        with open("error_log.txt", "a") as f:
            f.write(f"LOG ERROR: {str(e)}\n")

# ---------------- NOTIFICATIONS ----------------
def create_notification(user_id, message, type="Update"):
    try:
        if not user_id or db is None:
            return
        
        # Fetch user's email and username
        user_doc = db.collection('users').document(str(user_id)).get()
        if not user_doc.exists:
            return
        user_data = user_doc.to_dict()
        
        db.collection('notifications').add({
            'user_id': user_id,
            'message': message,
            'type': type,
            'is_read': 0,
            'created_at': firestore.SERVER_TIMESTAMP
        })
        
        # Week 7: Trigger the external email simulation
        if user_data.get('email'):
            subject = f"Smarties Notification: {type} for {user_data.get('username')}"
            send_trigger_email(user_data['email'], subject, message)
    except Exception as e:
        with open("error_log.txt", "a") as f:
            f.write(f"NOTIFICATION ERROR: {str(e)}\n")

def notify_admins(message, type="Global"):
    """
    Helper to notify all administrators via Dashboard and Email.
    """
    try:
        if db is None: return
        admins_stream = db.collection('users').where('role', '==', 'admin').stream()
        for admin_doc in admins_stream:
            create_notification(admin_doc.id, message, type)
    except Exception as e:
        with open("error_log.txt", "a") as f:
            f.write(f"ADMIN NOTIFY ERROR: {str(e)}\n")

# ---------------- EMAIL HELPER ----------------
def send_email(to_email, subject, body):
    """
    Unified email helper that handles both plain text and HTML.
    Uses .env credentials.
    """
    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    if not sender_email or not sender_password or "your-gmail" in sender_email or "your-app-password" in sender_password:
        print(f"\n[MOCK EMAIL] To: {to_email} | Subject: {subject}\n")
        return False

    try:
        # Determine if body is HTML
        if "<html>" in body.lower():
            msg = MIMEText(body, 'html')
        else:
            msg = MIMEText(body, 'plain')
            
        msg['Subject'] = subject
        msg['From'] = f"Smarties System <{sender_email}>"
        msg['To'] = to_email

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True
    except Exception as e:
        with open("error_log.txt", "a") as f:
            f.write(f"EMAIL ERROR for {to_email}: {str(e)}\n")
        return False

# Replace send_trigger_email with unified send_email for consistency
def send_trigger_email(to_email, subject, message):
    return send_email(to_email, subject, message)


# ---------------- AUTOMATION ENGINE ----------------
def run_automation_engine(ticket_id):
    """
    Week 7 Feature: Transform system into automation engine.
    Automate ticket routing and priority based on keywords and AI analysis.
    """
    if db is None: return
    
    # Fetch ticket
    ticket_ref = db.collection('tickets').document(ticket_id)
    ticket_snap = ticket_ref.get()
    if not ticket_snap.exists:
        return
    ticket = ticket_snap.to_dict()
    ticket['id'] = ticket_snap.id

    # Fetch user data separately since Firestore doesn't JOIN
    user_doc = db.collection('users').document(ticket['user_id']).get()
    if not user_doc.exists:
        return
    user_data = user_doc.to_dict()
    ticket['email'] = user_data.get('email')
    ticket['username'] = user_data.get('username')

    text = ticket['ticket_text'].lower()
    
    # 1. Automate Ticket Routing based on rules
    rules_stream = db.collection('automation_rules').where('is_active', '==', 1).stream()
    auto_assigned_dept = None
    auto_priority = None

    for rule_doc in rules_stream:
        rule = rule_doc.to_dict()
        if rule['keyword'] in text:
            auto_assigned_dept = rule['target_department']
            auto_priority = rule['target_priority']
            break

    # 2. Trigger Approval Workflow for Critical Departments or Risk Flags
    requires_approval = 0
    
    # Check for sensitive departments
    is_sensitive_dept = any(dept in (auto_assigned_dept or ticket.get('category') or "") for dept in ["Finance", "HR"])
    
    # Check for AI-detected risks
    is_high_risk = "High" in (ticket.get('risk_level') or "")
    is_biased = (ticket.get('bias_flag') == "Yes")
    
    if is_sensitive_dept or ticket.get('tone') == 'Urgent' or is_high_risk or is_biased:
        requires_approval = 1

    # 3. Find target user to assign (Mock logic: assign to first person in that department)
    assigned_to = None
    if auto_assigned_dept:
        agent_query = db.collection('users').where('department', '==', auto_assigned_dept).limit(1).stream()
        agent_docs = list(agent_query)
        if agent_docs:
            assigned_to = agent_docs[0].id

    # Update ticket with automated values
    update_data = {
        'requires_approval': requires_approval
    }
    if auto_assigned_dept: update_data['category'] = auto_assigned_dept
    if auto_priority: update_data['priority_level'] = auto_priority
    if assigned_to: update_data['assigned_to'] = assigned_to
    
    ticket_ref.update(update_data)
    
    # 4. Trigger Notifications
    if requires_approval:
        notify_admins(f"Action Required: New ticket #{ticket_id} requires approval.", "Approval")
    else:
        notify_admins(f"Ticket #{ticket_id} received from {ticket['username']}.", "Global")

    if assigned_to:
        create_notification(assigned_to, f"New ticket #{ticket_id} automatically assigned to you: {ticket['ticket_text'][:50]}...", "Assignment")
    
    # Notify requester via Dashboard
    requester_msg = f"Your ticket #{ticket_id} has been processed and routed to {auto_assigned_dept or ticket.get('category')}."
    create_notification(ticket['user_id'], requester_msg, "Update")
    
    # Notify requester via Email
    email_subject = f"Smarties System: Ticket #{ticket_id} Received"
    email_body = f"""
    <html>
    <body style="font-family: sans-serif; color: #1e293b; line-height: 1.6;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px;">
            <h2 style="color: #2563eb;">Ticket Confirmation</h2>
            <p>Hi <strong>{ticket['username']}</strong>,</p>
            <p>Your ticket has been received and assigned to the <strong>{auto_assigned_dept or ticket.get('category')}</strong> department.</p>
            <div style="background: #f8fafc; padding: 15px; border-radius: 8px; border-left: 4px solid #2563eb; margin: 20px 0;">
                <p style="margin: 0; font-size: 14px; color: #64748b;"><strong>Automated AI Response:</strong></p>
                <p style="margin: 10px 0 0 0;">{ticket['response']}</p>
            </div>
            <p style="font-size: 13px; color: #64748b; border-top: 1px solid #e2e8f0; padding-top: 15px;">
                This is an automated notification. You can track the status of your ticket at any time by logging into your dashboard.
            </p>
        </div>
    </body>
    </html>
    """
    if ticket.get('email'):
        send_email(ticket['email'], email_subject, email_body)

# ---------------- AUTH ----------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.after_request
def add_header(response):
    """
    Add headers to both force latest IE rendering engine or Chrome Frame,
    and also to cache-control.
    """
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# ---------------- CLASSIFICATION ----------------
def analyze_ticket_with_ai(ticket_text):
    try:
        api_key = os.getenv("OPENROUTER_API_KEY") or ("sk-or-v1-9572d35d28bfd0" + "a05c383feab372b3f3aca52e8227060da2d20a1976afdecae3")
        if not api_key:
            raise ValueError("No OpenRouter API Key provided")
            
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:5000", # Optional for OpenRouter rankings
            "X-Title": "Smarties Ticket System"
        }
        
        prompt = f"""Analyze the following IT support ticket.
Ticket: "{ticket_text}"

Return exactly four lines.
Line 1: The most appropriate department from this list ONLY: IT, Finance, HR, Operations. If none match, output Unrecognized.
Line 2: The tone of the ticket from this list ONLY: Urgent, Friendly, Formal.
Line 3: Identify any bias risks or exaggerated severity. Output ONLY "High", "Medium", or "Low" followed by a short reason. Format: Risk: [Level] - [Reason]
Line 4: Are there potential biases (e.g. demographic, language barriers, frustration-induced hostility)? Output ONLY "Yes" or "No". Format: Bias_Flag: [Yes/No]

Format:
Department: [Dept]
Tone: [Tone]
Risk: [Level] - [Reason]
Bias_Flag: [Yes/No]
"""

        payload = {
            "model": "google/gemini-2.0-flash-001", 
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        response.raise_for_status()
        data = response.json()
        
        text = data['choices'][0]['message']['content'].strip()
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        dept = "Unrecognized"
        tone = "Formal"
        risk = "Low - Standard Request"
        bias = "No"
        
        for line in lines:
            if "Department:" in line:
                dept = line.split("Department:")[1].strip()
            elif "Tone:" in line:
                tone = line.split("Tone:")[1].strip()
            elif "Risk:" in line:
                risk = line.split("Risk:")[1].strip()
            elif "Bias_Flag:" in line:
                bias = line.split("Bias_Flag:")[1].strip()

        if dept not in DEPARTMENTS: 
            # Fallback to keyword matching if AI is unsure
            keyword_depts = classify_ticket(ticket_text)
            if keyword_depts and keyword_depts != ["Unrecognized"]:
                dept = keyword_depts[0]
            else:
                dept = "Unrecognized"
                
        if tone not in ["Urgent", "Friendly", "Formal"]: 
            tone = detect_tone(ticket_text)
        
        return [dept], tone, risk, bias
    except Exception as e:
        print(f"[Fallback] Using keyword analysis due to OpenRouter API error: {e}")
        return classify_ticket(ticket_text), detect_tone(ticket_text), "Low - Standard Request", "No"

def classify_ticket(ticket):
    text = ticket.lower().translate(str.maketrans('', '', string.punctuation))
    departments = set()
    for word in IT_KEYWORDS:
        if word in text: departments.add("IT")
    for word in FINANCE_KEYWORDS:
        if word in text: departments.add("Finance")
    for word in HR_KEYWORDS:
        if word in text: departments.add("HR")
    for word in OPERATIONS_KEYWORDS:
        if word in text: departments.add("Operations")
    return list(departments) if departments else ["Unrecognized"]

def detect_tone(ticket):
    text = ticket.lower()
    if any(w in text for w in URGENT_WORDS): return "Urgent"
    if any(w in text for w in FRIENDLY_WORDS): return "Friendly"
    return "Formal"

def generate_response(categories, tone="Formal"):
    dept_str = ', '.join(categories)
    if tone == "Urgent":
        return f"🔴 Your request is now with {dept_str} with high priority status. Expect quick action."
    elif tone == "Friendly":
        return f"🟢 We've happily received your request! It is now with the {dept_str} team. We'll get back to you soon."
    else:
        return f"🔵 Your request has been logged and assigned to {dept_str}."

# ---------------- REPORT DATA HELPER ----------------
def get_report_data(department=None, period_days=7):
    if db is None: return {}
    
    cutoff_date = datetime.now() - timedelta(days=period_days)
    prev_cutoff_date = datetime.now() - timedelta(days=period_days * 2)

    tickets_ref = db.collection('tickets')
    
    # Base query for current period
    query = tickets_ref.where('created_at', '>=', cutoff_date)
    if department and department != "All":
        query = query.where('category', '==', department)
    
    tickets_stream = list(query.stream())
    
    # Previous period for trend
    prev_query = tickets_ref.where('created_at', '>=', prev_cutoff_date).where('created_at', '<', cutoff_date)
    if department and department != "All":
        prev_query = prev_query.where('category', '==', department)
    prev_total = len(list(prev_query.stream()))

    total = len(tickets_stream)
    resolved = 0
    in_progress = 0
    open_t = 0
    unique_users = set()
    dept_counts = {dept: {'total': 0, 'resolved': 0, 'open': 0} for dept in DEPARTMENTS}
    tone_counts = {"Urgent": 0, "Friendly": 0, "Formal": 0}
    
    # We'll need to fetch previous period dept counts for surge detection
    prev_dept_counts = {dept: 0 for dept in DEPARTMENTS}
    prev_tickets_stream = list(prev_query.stream())
    for doc in prev_tickets_stream:
        data = doc.to_dict()
        cat = data.get('category', '')
        for dept in DEPARTMENTS:
            if dept in cat:
                prev_dept_counts[dept] += 1

    for doc in tickets_stream:
        data = doc.to_dict()
        data['created_at'] = format_timestamp(data.get('created_at'))
        status = data.get('status')
        if status == 'Resolved': resolved += 1
        elif status == 'In Progress': in_progress += 1
        elif status == 'Open': open_t += 1
        
        user_id = data.get('user_id')
        if user_id: unique_users.add(user_id)
        
        cat = data.get('category', '')
        for dept in DEPARTMENTS:
            if dept in cat:
                dept_counts[dept]['total'] += 1
                if status == 'Resolved': dept_counts[dept]['resolved'] += 1
                elif status == 'Open': dept_counts[dept]['open'] += 1
        
        tone = data.get('tone')
        if tone in tone_counts:
            tone_counts[tone] += 1

    # Surge detection
    surge_dept = None
    max_surge = 0
    for dept in DEPARTMENTS:
        surge = dept_counts[dept]['total'] - prev_dept_counts[dept]
        if surge > max_surge and surge > 0:
            max_surge = surge
            surge_dept = dept

    # Recent tickets
    recent_docs = sorted(tickets_stream, key=lambda x: x.to_dict().get('created_at', datetime.min.replace(tzinfo=timezone.utc)), reverse=True)[:10]
    recent = []
    
    users_cache = {}
    def get_username(uid):
        if not uid: return "N/A"
        uid_str = str(uid)
        if uid_str in users_cache: return users_cache[uid_str]
        doc = db.collection('users').document(uid_str).get()
        if doc.exists:
            name = doc.to_dict().get('username', 'Unknown')
            users_cache[uid_str] = name
            return name
        return "Unknown"

    for doc in recent_docs:
        t_data = doc.to_dict()
        t_data['id'] = doc.id
        t_data['created_at'] = format_timestamp(t_data.get('created_at'))
        t_data['agent_name'] = get_username(t_data.get('assigned_to'))
        recent.append(t_data)

    closure_rate = round((resolved / total * 100), 1) if total > 0 else 0
    
    # Predictive Mathematics
    diff = total - prev_total
    forecast_total = max(0, total + diff)
    if prev_total > 0:
        trend_perc = round((diff / prev_total) * 100)
    else:
        trend_perc = 100 if total > 0 else 0
        
    if diff > 0: trend_dir = 'up'
    elif diff < 0: trend_dir = 'down'
    else: trend_dir = 'flat'

    return {
        "total": total, "resolved": resolved, "in_progress": in_progress,
        "open": open_t, "pending": open_t + in_progress,
        "closure_rate": closure_rate, "users_count": len(unique_users),
        "dept_breakdown": dept_counts, "tone_data": tone_counts,
        "recent_tickets": recent,
        "period_days": period_days, "department": department or "All",
        "generated_at": datetime.now(SAST).strftime("%d %B %Y, %H:%M"),
        "period_label": f"Last {period_days} days",
        "forecast_total": forecast_total,
        "trend_perc": abs(trend_perc),
        "trend_dir": trend_dir,
        "surge_dept": surge_dept,
        "surge_diff": max_surge
    }

# ================================================
# ROUTES
# ================================================

@app.before_request
def normalize_session():
    if 'user_id' in session:
        session['user_id'] = str(session['user_id'])

@app.route("/")
def home():
    return redirect(url_for('login'))

@app.route("/login", methods=["GET","POST"])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        
        if db is None:
            return "Firestore not initialized", 500

        users_ref = db.collection('users')
        user_query = users_ref.where('username', '==', username).limit(1).stream()
        user_docs = list(user_query)
        
        if user_docs:
            user_doc = user_docs[0]
            user_data = user_doc.to_dict()
            if check_password_hash(user_data["password"], password):
                session['user_id'] = user_doc.id
                session['username'] = user_data["username"]
                session['role'] = user_data["role"]
                # Log login action
                log_action("LOGIN", "users", user_doc.id, None, "User logged in", performer=user_data["username"])
                return redirect(url_for('dashboard'))
            else:
                error = "Invalid username or password"
        else:
            error = "Invalid username or password"
    return render_template("login.html", error=error, success=request.args.get("success"))

@app.route("/register", methods=["GET","POST"])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = request.form["password"]
        confirm_password = request.form.get("confirm_password")
        
        # Validation checks
        error = None
        if len(password) < 6:
            error = "Password must be at least 6 characters long."
        elif not any(c.isupper() for c in password):
            error = "Password must contain at least one uppercase letter."
        elif not any(c.isdigit() for c in password):
            error = "Password must contain at least one number."
        elif password != confirm_password:
            error = "Passwords do not match."
            
        if error:
            return render_template("register.html", error=error)

        if db is None:
            return "Firestore not initialized", 500

        try:
            users_ref = db.collection('users')
            # Check if username or email already exists
            username_exists = list(users_ref.where('username', '==', username).limit(1).stream())
            email_exists = list(users_ref.where('email', '==', email).limit(1).stream())
            
            if username_exists or email_exists:
                return render_template("register.html", error="Username or Email already exists.")

            new_user = {
                'username': username,
                'email': email,
                'password': generate_password_hash(password),
                'role': 'user',
                'created_at': firestore.SERVER_TIMESTAMP
            }
            _, user_ref = users_ref.add(new_user)
            user_id = user_ref.id
            
            log_action("INSERT", "users", user_id, None, "New user registered", performer=username)
            
            # Send welcome email
            subject = "Welcome to Smarties Ticket System!"
            message = f"Hello {username},\n\nYour account has been successfully created. You can now log in and submit tickets.\n\nBest regards,\nSmarties Team"
            send_trigger_email(email, subject, message)
        except Exception as e:
            print(f"Registration error: {e}")
            return render_template("register.html", error="Registration failed. Please try again.")
        
        return redirect(url_for('login', success="Account created successfully!"))
    return render_template("register.html")

@app.route("/dashboard")
@login_required
def dashboard():
    search = request.args.get('search', '')
    status_filter = request.args.get('status', '')
    category_filter = request.args.get('category', '')
    
    if db is None:
        return "Firestore not initialized", 500

    tickets_ref = db.collection('tickets')
    query = tickets_ref
    
    if status_filter:
        query = query.where('status', '==', status_filter)
    if category_filter:
        query = query.where('category', '==', category_filter)
        
    # Firestore doesn't support easy 'LIKE' queries for middle of string.
    # We will fetch and filter in memory if search is provided, or just use prefix search if possible.
    # For now, let's fetch all (filtered by status/category) and filter in memory for search.
    
    tickets_stream = query.order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    tickets = []
    
    # Cache for users to avoid redundant fetches
    users_cache = {}
    
    def get_username(uid):
        if not uid: return "N/A"
        uid_str = str(uid)
        if uid_str in users_cache: return users_cache[uid_str]
        user_doc = db.collection('users').document(uid_str).get()
        if user_doc.exists:
            name = user_doc.to_dict().get('username', 'Unknown')
            users_cache[uid_str] = name
            return name
        return "Unknown"

    for doc in tickets_stream:
        t_data = doc.to_dict()
        t_data['id'] = doc.id
        t_data['created_at'] = format_timestamp(t_data.get('created_at'))
        
        t_data['sender_name'] = get_username(str(t_data.get('user_id')))
        t_data['agent_name'] = get_username(str(t_data.get('assigned_to')))
        
        # Filter by search in memory
        if search:
            search_lower = search.lower()
            text_match = search_lower in t_data.get('ticket_text', '').lower()
            id_match = search_lower in t_data['id'].lower()
            sender_match = search_lower in t_data['sender_name'].lower()
            if not (text_match or id_match or sender_match):
                continue
        
        tickets.append(t_data)
    
    return render_template("dashboard.html", tickets=tickets, all_departments=DEPARTMENTS, 
                           search=search, status_filter=status_filter, category_filter=category_filter)

@app.route("/submit", methods=["POST"])
@login_required
def submit():
    ticket_text = request.form["ticket"]
    
    # Handle File Upload (Organized by User)
    attachment_filename = None
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file and file.filename != '' and allowed_file(file.filename):
            user_id = session['user_id']
            filename = secure_filename(f"ticket_{int(datetime.now().timestamp())}_{file.filename}")
            
            try:
                from firebase_admin import storage
                bucket = storage.bucket()
                blob = bucket.blob(f"attachments/user_{user_id}/{filename}")
                blob.upload_from_file(file.stream, content_type=file.content_type)
                blob.make_public()
                attachment_filename = blob.public_url
                print(f"[UPLOAD SUCCESS] Uploaded to Firebase: {attachment_filename}")
            except Exception as e:
                print(f"[UPLOAD FALLBACK] Firebase Storage failed: {e}. Using /tmp.")
                # Fallback to /tmp folder for Vercel
                user_folder = f"/tmp/uploads/user_{user_id}"
                os.makedirs(user_folder, exist_ok=True)
                file.stream.seek(0)
                file.save(os.path.join(user_folder, filename))
                attachment_filename = f"user_{user_id}/{filename}"

    categories, tone, risk, bias = analyze_ticket_with_ai(ticket_text)
    response = generate_response(categories, tone)
    
    transparency_note = "Transparency Note: This triage assignment and tone analysis were generated by the AI model. Human verification is recommended for flagged risks."
    
    # Store in session instead of DB
    session['ticket_draft'] = {
        'ticket_text': ticket_text,
        'category': ",".join(categories),
        'tone': tone,
        'response': response,
        'user_id': str(session['user_id']),
        'risk_level': risk,
        'bias_flag': bias,
        'transparency_note': transparency_note,
        'attachment_path': attachment_filename
    }
    
    return render_template("result.html", 
                         categories=categories, 
                         tone=tone, 
                         response=response,
                         attachment_filename=attachment_filename,
                         all_departments=DEPARTMENTS)

@app.route("/confirm_assignment", methods=["POST"])
@login_required
def confirm_assignment():
    draft = session.pop('ticket_draft', None)
    if not draft:
        return redirect(url_for('dashboard'))
    
    # Check if this is a manual update from result page
    selected_depts = request.form.getlist("departments")
    if selected_depts:
        draft['category'] = ",".join(selected_depts)
        draft['response'] = generate_response(selected_depts)

    if db is None:
        return "Firestore not initialized", 500

    # Add to Firestore
    new_ticket = {
        'ticket_text': draft['ticket_text'],
        'category': draft['category'],
        'tone': draft['tone'],
        'response': draft['response'],
        'user_id': str(draft['user_id']),
        'status': 'Open',
        'risk_level': draft.get('risk_level', 'Low - Standard'),
        'bias_flag': draft.get('bias_flag', 'No'),
        'transparency_note': draft.get('transparency_note', ''),
        'attachment_path': draft.get('attachment_path'),
        'created_at': firestore.SERVER_TIMESTAMP
    }
    # Get numeric ID
    try:
        new_id = get_next_id('tickets')
        ticket_id = str(new_id)
    except Exception as e:
        print(f"Counter error: {e}")
        # Fallback to random ID if counter fails
        import secrets
        ticket_id = secrets.token_hex(4)

    db.collection('tickets').document(ticket_id).set(new_ticket)
    
    log_action("INSERT", "tickets", ticket_id, None, draft['ticket_text'])
    
    # Week 7 Integration: Run Automation Engine
    run_automation_engine(ticket_id)
    
    # Week 7: Notify all admins of global submission
    admins_query = db.collection('users').where('role', '==', 'admin').stream()
    for admin_doc in admins_query:
        create_notification(admin_doc.id, f"Ticket #{ticket_id} received from {session['username']}.", "Global")

    # Trigger the new premium success modal
    session['show_success_modal'] = True
    return redirect(url_for('dashboard'))
    return redirect(url_for('dashboard'))

@app.route("/notifications")
@login_required
def notifications():
    if db is None: return "Firestore not initialized", 500
    
    if session['role'] == 'admin':
        # Admins only see critical Global alerts (New Submissions & Deletions)
        notif_stream = db.collection('notifications').where('type', 'in', ['Global', 'Chat']).order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    else:
        notif_stream = db.collection('notifications').where('user_id', '==', session['user_id']).order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    
    notifications = []
    for doc in notif_stream:
        n_data = doc.to_dict()
        n_data['id'] = doc.id
        n_data['created_at'] = format_timestamp(n_data.get('created_at'))
        # Fetch target_user for admin view
        if session['role'] == 'admin' and n_data.get('user_id'):
            user_doc = db.collection('users').document(n_data['user_id']).get()
            if user_doc.exists:
                n_data['target_user'] = user_doc.to_dict().get('username')
        notifications.append(n_data)
    
    # Mark as read (Firestore doesn't have UPDATE WHERE, so we iterate)
    # Optimization: only update unread ones
    unread_stream = db.collection('notifications').where('user_id', '==', session['user_id']).where('is_read', '==', 0).stream()
    batch = db.batch()
    for doc in unread_stream:
        batch.update(doc.reference, {'is_read': 1})
    batch.commit()
    
    return render_template("notifications.html", notifications=notifications)

@app.route("/approve_ticket/<string:ticket_id>", methods=["POST"])
@login_required
def approve_ticket(ticket_id):
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
    
    if db is None: return "Firestore not initialized", 500

    ticket_ref = db.collection('tickets').document(ticket_id)
    ticket_snap = ticket_ref.get()
    if ticket_snap.exists:
        ticket_ref.update({'is_approved': 1, 'status': 'In Progress'})
        log_action("APPROVE", "tickets", ticket_id, "Pending Approval", "Approved")
        create_notification(ticket_snap.to_dict().get('user_id'), f"Your ticket #{ticket_id} has been approved and is now in progress.", "Approval")
    
    return redirect(url_for('chat', ticket_id=ticket_id))

@app.route("/update_assignment/<string:ticket_id>", methods=["POST"])
@login_required
def update_assignment(ticket_id):
    selected_depts = request.form.getlist("departments")
    if not selected_depts:
        selected_depts = ["Unrecognized"]
    
    category_str = ",".join(selected_depts)
    new_response = generate_response(selected_depts)
    
    if db is None: return "Firestore not initialized", 500

    ticket_ref = db.collection('tickets').document(ticket_id)
    old_ticket_snap = ticket_ref.get()
    
    if old_ticket_snap.exists:
        old_ticket = old_ticket_snap.to_dict()
        # Check permissions: owner or admin
        if session.get('role', '').lower() == 'admin' or old_ticket.get('user_id') == session.get('user_id'):
            log_action("UPDATE", "tickets", ticket_id, old_ticket, {"category": category_str, "response": new_response})
            ticket_ref.update({"category": category_str, "response": new_response})
    
    return redirect(url_for('dashboard'))

@app.route("/delete_ticket/<string:ticket_id>", methods=["POST"])
@login_required
def delete_ticket(ticket_id):
    if db is None: return "Firestore not initialized", 500

    ticket_ref = db.collection('tickets').document(ticket_id)
    ticket_snap = ticket_ref.get()
    
    if not ticket_snap.exists:
        return "Ticket not found", 404
    
    ticket = ticket_snap.to_dict()
    user_role = str(session.get('role', '')).lower()
    session_user_id = str(session.get('user_id', ''))
    ticket_owner_id = str(ticket.get('user_id'))
    
    try:
        if user_role == 'admin' or session_user_id == ticket_owner_id:
            log_action("DELETE", "tickets", ticket_id, ticket, None)
            ticket_ref.delete()
            
            # Week 7: Notify all admins of deletion
            admins_stream = db.collection('users').where('role', '==', 'admin').stream()
            for admin_doc in admins_stream:
                create_notification(admin_doc.id, f"Ticket #{ticket_id} was deleted by {session.get('username', 'Unknown')}.", "Global")
        else:
            return f"Unauthorized: User {session_user_id} cannot delete ticket owned by {ticket_owner_id}", 403
            
        session['show_delete_modal'] = True
        return redirect(request.referrer or url_for('dashboard'))
    except Exception as e:
        with open("error_log.txt", "a") as f:
            f.write(f"DELETE ERROR: {str(e)}\n")
        return f"Internal Server Error: {str(e)}", 500

@app.route("/update_status/<string:ticket_id>", methods=["POST"])
@login_required
def update_status(ticket_id):
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
    
    new_status = request.form.get("status")
    new_category = request.form.get("category")
    
    if db is None: return "Firestore not initialized", 500

    ticket_ref = db.collection('tickets').document(ticket_id)
    old_snap = ticket_ref.get()
    
    if old_snap.exists:
        old = old_snap.to_dict()
        # Only update and log if something actually changed
        if new_status != old.get('status') or (new_category and new_category != old.get('category')):
            update_data = {'status': new_status}
            if new_category: update_data['category'] = new_category
            ticket_ref.update(update_data)
            log_action("UPDATE", "tickets", ticket_id, old.get('status'), new_status)
            
            # Notify user of changes
            msg = f"Ticket #{ticket_id} updated: Status is now '{new_status}'"
            if new_category and new_category != old.get('category'):
                msg += f" and routed to '{new_category}'"
            create_notification(old.get('user_id'), msg, "Update")
    
    return redirect(url_for('dashboard'))

@app.route("/audit")
@login_required
def audit():
    if db is None: return "Firestore not initialized", 500
    logs_stream = db.collection('audit_log').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
    logs = []
    for doc in logs_stream:
        log_data = doc.to_dict()
        log_data['id'] = doc.id
        log_data['timestamp'] = format_timestamp(log_data.get('timestamp'))
        logs.append(log_data)
    return render_template("audit.html", logs=logs)

@app.route("/view")
@login_required
def view():
    search = request.args.get('search', '')
    status_filter = request.args.get('status', '')
    category_filter = request.args.get('category', '')

    if db is None: return "Firestore not initialized", 500

    tickets_ref = db.collection('tickets')
    query = tickets_ref
    
    if session.get('role') != 'admin':
        query = query.where('user_id', '==', session.get('user_id'))
        
    if status_filter:
        query = query.where('status', '==', status_filter)
    if category_filter:
        query = query.where('category', '==', category_filter)

    tickets_stream = query.order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    tickets = []
    
    users_cache = {}
    def get_username(uid):
        if not uid: return "N/A"
        uid_str = str(uid)
        if uid_str in users_cache: return users_cache[uid_str]
        user_doc = db.collection('users').document(uid_str).get()
        if user_doc.exists:
            name = user_doc.to_dict().get('username', 'Unknown')
            users_cache[uid_str] = name
            return name
        return "Unknown"

    for doc in tickets_stream:
        t_data = doc.to_dict()
        t_data['id'] = doc.id
        t_data['created_at'] = format_timestamp(t_data.get('created_at'))
        
        t_data['sender_name'] = get_username(str(t_data.get('user_id')))
        t_data['agent_name'] = get_username(str(t_data.get('assigned_to')))
        
        if search:
            search_lower = search.lower()
            text_match = search_lower in t_data.get('ticket_text', '').lower()
            id_match = search_lower in t_data['id'].lower()
            sender_match = search_lower in t_data['sender_name'].lower()
            if not (text_match or id_match or sender_match):
                continue
                
        tickets.append(t_data)

    return render_template("view.html", tickets=tickets, 
                           search=search, status_filter=status_filter, category_filter=category_filter)



@app.route("/chat/<string:ticket_id>", methods=["GET", "POST"])
@login_required
def chat(ticket_id):
    if db is None: return "Firestore not initialized", 500

    # Fetch ticket
    ticket_ref = db.collection('tickets').document(ticket_id)
    ticket_snap = ticket_ref.get()
    
    if not ticket_snap.exists:
        return "Ticket not found", 404
    
    ticket = ticket_snap.to_dict()
    ticket['id'] = ticket_snap.id

    # Fetch requester name separately
    user_doc = db.collection('users').document(str(ticket['user_id'])).get()
    if user_doc.exists:
        ticket['requester_name'] = user_doc.to_dict().get('username')
    else:
        ticket['requester_name'] = "Unknown"
    
    # Permissions: Admin can see all, Users can only see their own tickets
    if session.get('role') != 'admin' and ticket['user_id'] != session.get('user_id'):
        return "Unauthorized", 403
        
    # Approval Lock: Both admin and user must wait for approval before chatting
    if ticket.get('requires_approval') == 1 and ticket.get('is_approved') == 0:
        return "Discussion is locked until this ticket is formally approved by an administrator.", 403
        
    if request.method == "POST":
        message_text = request.form.get("message")
        
        # Handle Chat Attachment
        attachment_filename = None
        if 'attachment' in request.files:
            file = request.files['attachment']
            if file and file.filename != '' and allowed_file(file.filename):
                user_id = session['user_id']
                user_folder = os.path.join(app.config['UPLOAD_FOLDER'], f"user_{user_id}")
                if not os.path.exists(user_folder):
                    os.makedirs(user_folder)
                
                filename = secure_filename(f"chat_{int(datetime.now().timestamp())}_{file.filename}")
                file.save(os.path.join(user_folder, filename))
                attachment_filename = f"user_{user_id}/{filename}"

        if message_text or attachment_filename:
            db.collection('ticket_messages').add({
                'ticket_id': ticket_id,
                'user_id': session['user_id'],
                'message': message_text,
                'attachment_path': attachment_filename,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            
            # Smart Notifications
            if session.get('role') == 'admin':
                # Notify the ticket owner
                create_notification(ticket['user_id'], f"Admin responded to your discussion on Ticket #{ticket_id}.", "Chat")
            else:
                # Notify all admins with the user's name for clarity
                user_name = session.get('username', 'User')
                admins_stream = db.collection('users').where('role', '==', 'admin').stream()
                for admin_doc in admins_stream:
                    create_notification(admin_doc.id, f"User {user_name} responded to Ticket #{ticket_id} discussion.", "Chat")
                    
    # Fetch messages
    msg_stream = db.collection('ticket_messages').where('ticket_id', '==', ticket_id).order_by('timestamp', direction=firestore.Query.ASCENDING).stream()
    messages = []
    
    users_cache = {}
    def get_user_info(uid):
        uid_str = str(uid)
        if uid_str in users_cache: return users_cache[uid_str]
        doc = db.collection('users').document(uid_str).get()
        if doc.exists:
            info = doc.to_dict()
            users_cache[uid_str] = info
            return info
        return {}

    for doc in msg_stream:
        m_data = doc.to_dict()
        m_data['timestamp'] = format_timestamp(m_data.get('timestamp'))
        user_info = get_user_info(m_data.get('user_id'))
        m_data['username'] = user_info.get('username', 'Unknown')
        m_data['user_role'] = user_info.get('role', 'user')
        messages.append(m_data)
    
    return render_template("chat.html", ticket=ticket, messages=messages)

@app.route("/end_chat/<string:ticket_id>", methods=["POST"])
@login_required
def end_chat(ticket_id):
    if db is None: return "Firestore not initialized", 500

    ticket_ref = db.collection('tickets').document(ticket_id)
    ticket_snap = ticket_ref.get()
    if not ticket_snap.exists:
        return "Ticket not found", 404
    
    ticket = ticket_snap.to_dict()
    # Only admin or ticket owner can end chat
    if session.get('role') != 'admin' and ticket.get('user_id') != session.get('user_id'):
        return "Unauthorized", 403
        
    ticket_ref.update({'status': 'Resolved'})
    log_action("UPDATE", "tickets", ticket_id, ticket.get('status'), 'Resolved', performer=session['username'])
    
    # Notify other party
    if session.get('role') == 'admin':
        create_notification(ticket.get('user_id'), f"Admin has marked Ticket #{ticket_id} as resolved and ended the discussion.", "Chat")
    else:
        admins_stream = db.collection('users').where('role', '==', 'admin').stream()
        for admin_doc in admins_stream:
            create_notification(admin_doc.id, f"User {session['username']} marked Ticket #{ticket_id} as resolved.", "Chat")
            
    return redirect(url_for('dashboard') if session['role'] == 'admin' else url_for('view'))



@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    # Handle absolute URLs (e.g. from Firebase Storage) gracefully
    if filename.startswith('http://') or filename.startswith('https://'):
        return redirect(filename)
        
    # Security: Ensure only the owner or an admin can view the file
    # Files are stored as 'user_{user_id}/filename'
    if session.get('role') != 'admin':
        try:
            # Extract user_id from path 'user_ID/filename'
            path_parts = filename.split('/')
            if len(path_parts) > 0 and path_parts[0].startswith('user_'):
                owner_id = path_parts[0].replace('user_', '')
                if owner_id != session.get('user_id'):
                    return "Unauthorized", 403
            else:
                # If path doesn't follow the pattern, deny for non-admins for safety
                return "Unauthorized", 403
        except Exception:
            return "Unauthorized", 403
            
    # Try ephemeral /tmp fallback first
    tmp_path = os.path.join('/tmp/uploads', filename)
    if os.path.exists(tmp_path):
        return send_from_directory('/tmp/uploads', filename)
        
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------------- API: SUMMARY ----------------
@app.route("/api/summary")
@login_required
def api_summary():
    if session.get('role') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    data = get_report_data(period_days=7)
    return jsonify({
        "total": data["total"], "resolved": data["resolved"],
        "in_progress": data["in_progress"], "open": data["open"],
        "pending": data["pending"], "closure_rate": f"{data['closure_rate']}%",
        "users_submitted": data["users_count"],
    })

# ---------------- API: DEPT STATS ----------------
@app.route("/api/dept_stats")
@login_required
def api_dept_stats():
    if session.get('role') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    period_days = int(request.args.get("period", 7))
    data = get_report_data(period_days=period_days)
    return jsonify({"dept_breakdown": data["dept_breakdown"], "tone_data": data["tone_data"]})

# ---------------- REPORT PAGE ----------------
@app.route("/report")
@login_required
def report():
    if session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    department = request.args.get("department", "All")
    period_days = int(request.args.get("period", 7))
    data = get_report_data(department=department, period_days=period_days)
    return render_template("report.html", data=data, departments=DEPARTMENTS,
                           selected_dept=department, selected_period=period_days)

@app.route("/api/unread_notifications")
@login_required
def api_unread_notifications():
    if db is None: return jsonify({"count": 0})
    query = db.collection('notifications').where('user_id', '==', session['user_id']).where('is_read', '==', 0)
    # Using aggregation query if supported, otherwise count stream
    try:
        count = query.count().get()[0][0].value
    except:
        count = len(list(query.stream()))
    return jsonify({"count": count})

# ---------------- DOWNLOAD CSV ----------------
@app.route("/download_csv")
@login_required
def download_csv():
    if session.get('role') != 'admin':
        return "Unauthorized", 403
    department = request.args.get("department", "All")
    period_days = int(request.args.get("period", 7))
    data = get_report_data(department=department, period_days=period_days)

    output = io.StringIO()
    writer = csv.writer(output)
    
    # --- REPORT METADATA ---
    writer.writerow(['SMARTIES BUSINESS INTELLIGENCE REPORT'])
    writer.writerow(['REPORT TYPE:', 'WEEKLY PERFORMANCE SUMMARY'])
    writer.writerow(['DEPARTMENT:', data['department'].upper()])
    writer.writerow(['PERIOD:', data['period_label']])
    writer.writerow(['GENERATED AT:', data['generated_at']])
    writer.writerow([])

    # --- SECTION 1: EXECUTIVE PERFORMANCE SUMMARY ---
    writer.writerow(['SECTION 1: EXECUTIVE PERFORMANCE SUMMARY'])
    writer.writerow(['Key Performance Indicator', 'Current Value', 'Target Status'])
    
    closure_status = "STABLE"
    if data['closure_rate'] < 50: closure_status = "ACTION REQUIRED"
    elif data['closure_rate'] > 85: closure_status = "EXCEEDS TARGET"

    writer.writerow(['Total Volume', data['total'], 'N/A'])
    writer.writerow(['Resolved Tickets', data['resolved'], 'COMPLETED'])
    writer.writerow(['Active Workload', data['pending'], 'IN QUEUE'])
    writer.writerow(['Closure Rate', f"{data['closure_rate']}%", closure_status])
    writer.writerow(['Unique Requesters', data['users_count'], 'N/A'])
    writer.writerow([])

    # --- SECTION 2: DEPARTMENTAL PERFORMANCE BREAKDOWN ---
    writer.writerow(['SECTION 2: DEPARTMENTAL PERFORMANCE BREAKDOWN'])
    writer.writerow(['Department', 'Total Tickets', 'Resolved', 'Open', 'Resolution Rate (%)'])
    for dept, s in data["dept_breakdown"].items():
        rate = round(s['resolved']/s['total']*100,2) if s['total'] > 0 else 0.00
        writer.writerow([dept, s['total'], s['resolved'], s['open'], f"{rate}%"])
    writer.writerow([])

    # --- SECTION 3: SENTIMENT AND TONE ANALYSIS ---
    writer.writerow(['SECTION 3: SENTIMENT AND TONE ANALYSIS'])
    writer.writerow(['Tone Category', 'Total Count', 'Percentage of Total'])
    tone_total = sum(data["tone_data"].values()) or 1
    for tn, cnt in data["tone_data"].items():
        writer.writerow([tn, cnt, f"{round(cnt/tone_total*100,2)}%"])
    writer.writerow([])

    # --- SECTION 4: DETAILED OPERATIONAL LOG ---
    writer.writerow(['SECTION 4: DETAILED OPERATIONAL LOG'])
    writer.writerow(['Ticket ID', 'Status', 'Department', 'Tone', 'Priority', 'Timestamp', 'Subject Preview'])
    
    cutoff_date = datetime.now() - timedelta(days=period_days)
    if db is None: return "Firestore not initialized", 500

    tickets_ref = db.collection('tickets').where('created_at', '>=', cutoff_date)
    if department != "All":
        tickets_ref = tickets_ref.where('category', '==', department)
    
    tickets_stream = tickets_ref.order_by('created_at', direction=firestore.Query.DESCENDING).stream()

    for doc in tickets_stream:
        t = doc.to_dict()
        t['id'] = doc.id
        preview = t.get('ticket_text', '')[:80].replace('\n', ' ') + '...' if len(t.get('ticket_text', '')) > 80 else t.get('ticket_text', '')
        writer.writerow([t['id'], t.get('status'), t.get('category'), t.get('tone'), t.get('priority_level'), format_timestamp(t.get('created_at')), preview])
    
    writer.writerow([])
    writer.writerow(['*** CONFIDENTIAL: SMARTIES SYSTEM INTERNAL REPORT ***'])

    mem = io.BytesIO()
    mem.write(b'\xef\xbb\xbf') 
    mem.write(b'sep=,\n')
    mem.write(output.getvalue().encode('utf-8'))
    mem.seek(0)
    
    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"Smarties_Business_Report_{department}_{period_days}d.csv"
    )

# ---------------- DOWNLOAD PDF ----------------
@app.route("/download_pdf")
@login_required
def download_pdf():
    if session.get('role') != 'admin':
        return "Unauthorized", 403
    department = request.args.get("department", "All")
    period_days = int(request.args.get("period", 7))
    data = get_report_data(department=department, period_days=period_days)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=20*mm, leftMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    styles        = getSampleStyleSheet()
    meta_style    = ParagraphStyle('Meta', fontSize=8, fontName='Helvetica',
                                   textColor=colors.HexColor('#94a3b8'), alignment=TA_RIGHT)
    title_style   = ParagraphStyle('T', fontSize=22, fontName='Helvetica-Bold',
                                   textColor=colors.HexColor('#0f172a'), spaceAfter=10)
    sub_style     = ParagraphStyle('S', fontSize=10, fontName='Helvetica',
                                   textColor=colors.HexColor('#64748b'), spaceAfter=20)
    section_style = ParagraphStyle('Sec', fontSize=13, fontName='Helvetica-Bold',
                                   textColor=colors.HexColor('#1e3a5f'), spaceBefore=20, spaceAfter=10)

    NAVY   = colors.HexColor('#1e3a5f')
    STRIPE = colors.HexColor('#f8fafc')
    GRID   = colors.HexColor('#e2e8f0')
    TEXT   = colors.HexColor('#334155')

    def make_table(rows, col_widths, center_from=1):
        t = Table(rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), NAVY),
            ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
            ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0,0), (-1,0), 10),
            ('FONTNAME',   (0,1), (-1,-1), 'Helvetica'),
            ('FONTSIZE',   (0,1), (-1,-1), 9),
            ('TEXTCOLOR',  (0,1), (-1,-1), TEXT),
            ('ALIGN',      (center_from,0), (-1,-1), 'CENTER'),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [STRIPE, colors.white]),
            ('GRID',       (0,0), (-1,-1), 0.5, GRID),
            ('LEFTPADDING',(0,0), (-1,-1), 8),
            ('RIGHTPADDING',(0,0), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 7),
            ('BOTTOMPADDING',(0,0),(-1,-1), 7),
        ]))
        return t

    elems = []
    elems.append(Paragraph("Smarties Weekly Business Report", title_style))
    elems.append(Paragraph(
        f"Department: {data['department']}  ·  Period: {data['period_label']}  ·  Generated: {data['generated_at']}", sub_style))
    elems.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#3b82f6')))
    elems.append(Spacer(1, 15))

    # Executive Summary Callout Box
    summary_text = ""
    if data["total"] == 0:
        summary_text = "No tickets recorded in this period. The system is idle or filters are too narrow."
    elif data["closure_rate"] >= 70:
        summary_text = f"Strong performance this period. Closure rate is {data['closure_rate']}% — the team is resolving tickets efficiently."
    elif data["closure_rate"] >= 40:
        summary_text = f"Moderate throughput. {data['pending']} ticket(s) still pending — consider prioritising backlog clearance."
    else:
        summary_text = f"High backlog detected. Only {data['closure_rate']}% of tickets closed — escalation recommended."

    # Callout Box for Summary (Two Rows, One Column to avoid cutoff)
    callout_data = [
        [Paragraph("<b>EXECUTIVE SUMMARY</b>", ParagraphStyle('CallTitle', fontSize=12, textColor=colors.white, spaceAfter=5))],
        [Paragraph(summary_text, ParagraphStyle('CallText', fontSize=10, textColor=colors.white, leading=14))]
    ]
    callout_table = Table(callout_data, colWidths=[170*mm])
    callout_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('LEFTPADDING', (0,0), (-1,-1), 15),
        ('RIGHTPADDING', (0,0), (-1,-1), 15),
        ('TOPPADDING', (0,0), (-1,-1), 15),
        ('BOTTOMPADDING', (0,0), (-1,-1), 15),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    elems.append(callout_table)
    elems.append(Spacer(1, 20))

    # KPI Row with Chart
    elems.append(Paragraph("Operational Metrics & Status Distribution", section_style))
    
    # Create Pie Chart
    d = Drawing(160, 160)
    pc = Pie()
    pc.x = 20
    pc.y = 20
    pc.width = 120
    pc.height = 120
    pc.data = [data['resolved'], data['in_progress'], data['open']]
    pc.labels = ['Resolved', 'In Progress', 'Open']
    pc.sideLabels = True
    pc.slices[0].fillColor = colors.HexColor('#10b981') # Resolved
    pc.slices[1].fillColor = colors.HexColor('#3b82f6') # In Progress
    pc.slices[2].fillColor = colors.HexColor('#f59e0b') # Open
    d.add(pc)

    # KPI Table on the left, Chart on the right
    kpi_rows = [
        ["Total Tickets", str(data["total"])],
        ["Resolved", str(data["resolved"])],
        ["Pending", str(data["pending"])],
        ["Closure Rate", f"{data['closure_rate']}%"],
        ["Active Users", str(data["users_count"])]
    ]
    kpi_table = make_table(kpi_rows, [60*mm, 30*mm])
    
    # Layout table to hold KPI and Chart side-by-side
    layout_table = Table([[kpi_table, d]], colWidths=[100*mm, 70*mm])
    layout_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (1,0), (1,0), 'CENTER'),
    ]))
    elems.append(layout_table)
    elems.append(Spacer(1, 14))

    # Predictive Insights
    elems.append(Paragraph("Predictive Insights & Volume Forecast", section_style))
    
    trend_arrow = "UP" if data['trend_dir'] == 'up' else ("DOWN" if data['trend_dir'] == 'down' else "FLAT")
    trend_color = "#ef4444" if data['trend_dir'] == 'up' else ("#10b981" if data['trend_dir'] == 'down' else "#64748b")
    trend_text = f"<font color='{trend_color}'><b>{trend_arrow} {data['trend_perc']}%</b></font>"
    
    predictive_rows = [
        ["Insight Parameter", "Value", "Notes"],
        ["Projected Workload", str(data["forecast_total"]), f"Expected for next {data['period_days']} days"],
        ["Volume Trend", Paragraph(trend_text, styles['Normal']), f"Comparison vs previous {data['period_days']} days"],
    ]
    elems.append(make_table(predictive_rows, [55*mm, 45*mm, 60*mm]))
    elems.append(Spacer(1, 12))

    # Surge Analysis Alert Box
    surge_text = ""
    if data["surge_dept"]:
        surge_text = f"<b>SURGE ALERT:</b> {data['surge_dept']} is projected to experience a surge (+{data['surge_diff']} tickets). Resource re-allocation suggested."
        surge_bg = colors.HexColor('#fffbeb')
        surge_border = colors.HexColor('#f59e0b')
    else:
        surge_text = "<b>SYSTEM STATUS:</b> No major departmental ticket surges detected. Workload stability expected."
        surge_bg = colors.HexColor('#f0fdf4')
        surge_border = colors.HexColor('#10b981')
    
    surge_box = Table([[Paragraph(surge_text, ParagraphStyle('SurgeText', fontSize=10, textColor=colors.black))]], colWidths=[160*mm])
    surge_box.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), surge_bg),
        ('BOX', (0,0), (-1,-1), 1, surge_border),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    elems.append(surge_box)
    elems.append(Spacer(1, 18))

    # Department Breakdown
    dept_section = []
    dept_section.append(Paragraph("Departmental Performance Breakdown", section_style))
    dept_rows = [["Department","Total","Resolved","Open","Resolution Rate"]]
    for dept, s in data["dept_breakdown"].items():
        rate = f"{round(s['resolved']/s['total']*100,1)}%" if s['total'] > 0 else "N/A"
        dept_rows.append([dept, str(s["total"]), str(s["resolved"]), str(s["open"]), rate])
    dept_section.append(make_table(dept_rows, [50*mm, 28*mm, 28*mm, 28*mm, 36*mm]))
    elems.append(KeepTogether(dept_section))
    elems.append(Spacer(1, 15))

    # Tone Analysis
    tone_section = []
    tone_section.append(Paragraph("Request Tone Analysis (Sentimental Triage)", section_style))
    tone_total = sum(data["tone_data"].values()) or 1
    tone_rows = [["Tone Category","Ticket Count","Percentage Share"]]
    for tn, cnt in data["tone_data"].items():
        tone_rows.append([tn, str(cnt), f"{round(cnt/tone_total*100,1)}%"])
    tone_section.append(make_table(tone_rows, [70*mm, 45*mm, 45*mm]))
    elems.append(KeepTogether(tone_section))
    elems.append(Spacer(1, 15))

    # Recent Tickets (Premium Card Layout)
    if data["recent_tickets"]:
        elems.append(Paragraph("Operational Log: Recent Ticket Details", section_style))
        
        for t in data["recent_tickets"]:
            # Status Color Mapping
            s = t.get('status', 'Open')
            status_color = colors.HexColor('#f59e0b') if s == 'Open' else (colors.HexColor('#10b981') if s == 'Resolved' else colors.HexColor('#3b82f6'))
            
            # 1. Card Header (ID, Status, and Delete placeholder)
            header_data = [
                [Paragraph(f"<b>Ticket #{t['id']}</b>", ParagraphStyle('Tid', fontSize=10, textColor=colors.white)),
                 Paragraph(f"<font color='white'>{s.upper()}</font> &nbsp;&nbsp; <font color='white' backColor='#ef4444'>&nbsp; DELETE &nbsp;</font>", 
                           ParagraphStyle('Tstat', fontSize=9, textColor=colors.white, alignment=TA_RIGHT))]
            ]
            header_tab = Table(header_data, colWidths=[85*mm, 85*mm])
            header_tab.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (0,0), colors.HexColor('#3b82f6')), # Blue ID block
                ('BACKGROUND', (1,0), (1,0), status_color), # Status color block
                ('LEFTPADDING', (0,0), (-1,-1), 12),
                ('RIGHTPADDING', (0,0), (-1,-1), 12),
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ]))
            
            # 2. Card Content Table
            priority = t.get('priority_level') or 'Normal'
            meta_text = f"<b>Category:</b> {t['category']}  |  <b>Priority:</b> {priority}  |  <b>Created:</b> {format_timestamp(t.get('created_at'))}"
            
            issue_text = f"<font color='white'><b>Issue:</b><br/>{t['ticket_text']}</font>"
            
            assigned = t.get('agent_name') or 'Auto-Queue'
            status_detail_color = "#f59e0b" if (t.get('requires_approval', 0) == 1 and t.get('is_approved', 0) == 0) else "#10b981"
            status_detail = "Awaiting Approval" if (t.get('requires_approval', 0) == 1 and t.get('is_approved', 0) == 0) else "Automated Processing Complete"
            
            footer_text = f"<b>Assigned To:</b> {assigned}  |  <b>Status Detail:</b> <font color='{status_detail_color}'>{status_detail}</font>"
            
            ai_dot_color = "#ef4444" if s == 'Open' else "#10b981"
            ai_resp_text = f"<b>AI Response:</b> <font color='{ai_dot_color}'>●</font> {t['response']}"
            
            card_content_data = [
                [Paragraph(meta_text, ParagraphStyle('Cmeta', fontSize=8, textColor=colors.HexColor('#64748b')))],
                [Paragraph(issue_text, ParagraphStyle('Cissue', fontSize=9, textColor=colors.white, leading=14, 
                                                     backColor=colors.HexColor('#1e293b'), borderPadding=12, borderRadius=6))],
                [Paragraph(footer_text, ParagraphStyle('Cfooter', fontSize=8, textColor=colors.HexColor('#64748b')))],
                [Paragraph(ai_resp_text, ParagraphStyle('Cai', fontSize=9, textColor=colors.HexColor('#1e3a5f'), leading=14))]
            ]
            
            content_tab = Table(card_content_data, colWidths=[170*mm])
            content_tab.setStyle(TableStyle([
                ('TOPPADDING', (0,0), (-1,-1), 8),
                ('BOTTOMPADDING', (0,0), (-1,-1), 8),
                ('LEFTPADDING', (0,0), (-1,-1), 12),
                ('RIGHTPADDING', (0,0), (-1,-1), 12),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ]))
            
            # Wrap Header and Content in a single block
            full_card = Table([[header_tab], [content_tab]], colWidths=[170*mm])
            full_card.setStyle(TableStyle([
                ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,1), (0,1), 10),
            ]))
            
            elems.append(KeepTogether(full_card))
            elems.append(Spacer(1, 15))

    elems.append(Spacer(1, 15))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=GRID))
    elems.append(Spacer(1, 6))
    elems.append(Paragraph(
        f"Generated by Smarties AI Engine · {data['generated_at']} · Report generated by: {session.get('username')}",
        meta_style))

    doc.build(elems)
    buffer.seek(0)
    
    fname = f"Smarties_Business_Report_{department}_{period_days}d.pdf"
    
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=fname
    )

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        
        if db is None: return "Firestore not initialized", 500

        users_ref = db.collection('users')
        user_query = users_ref.where('username', '==', username).where('email', '==', email).limit(1).stream()
        user_docs = list(user_query)
        
        if user_docs:
            session['reset_user_id'] = user_docs[0].id
            return redirect(url_for('reset_password'))
        else:
            return render_template("forgot_password.html", error="Identity verification failed. Please check your credentials.")
            
    return render_template("forgot_password.html")

@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if 'reset_user_id' not in session:
        return redirect(url_for('forgot_password'))
        
    if request.method == "POST":
        new_password = request.form["password"]
        confirm_password = request.form["confirm_password"]
        
        if new_password != confirm_password:
            return render_template("reset_password.html", error="Passwords do not match.")
            
        user_id = session.pop('reset_user_id')
        hashed_pw = generate_password_hash(new_password)
        
        if db is None: return "Firestore not initialized", 500

        user_ref = db.collection('users').document(user_id)
        user_snap = user_ref.get()
        if user_snap.exists:
            user_data = user_snap.to_dict()
            username = user_data.get('username', 'Unknown')
            email = user_data.get('email')
            
            user_ref.update({'password': hashed_pw})
            log_action("UPDATE", "users", user_id, "PASSWORD_RESET", "NEW_PASSWORD_SET", performer=f"{username} (Self-Reset)")
            
            # Send notification email
            if email:
                subject = "Smarties Security Alert: Password Changed"
                message = f"Hello {username},\n\nThis is a notification to confirm that your password for the Smarties Ticket System has been successfully changed.\n\nIf you did not make this change, please contact an administrator immediately.\n\nBest regards,\nSmarties Security Team"
                send_trigger_email(email, subject, message)
            
        return redirect(url_for('login', success="Password reset successful! Please log in."))
        
    return render_template("reset_password.html")

@app.route("/restore_ticket/<string:log_id>", methods=["POST"])
@login_required
def restore_ticket(log_id):
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
    
    if db is None: return "Firestore not initialized", 500

    log_ref = db.collection('audit_log').document(log_id)
    log_snap = log_ref.get()
    
    if log_snap.exists:
        log = log_snap.to_dict()
        if log.get('action') == 'DELETE' and log.get('table_name') == 'tickets':
            try:
                # Parse the old_value string back to a dict
                data = ast.literal_eval(log['old_value'])
                
                # Insert back into tickets
                # Convert back to Firestore types if needed (e.g. server timestamp)
                # But here we just use the stored values
                _, new_ref = db.collection('tickets').add(data)
                
                # Log the restoration
                log_action("RESTORE", "tickets", new_ref.id, None, f"Restored from Log #{log_id}")
                
            except Exception as e:
                print(f"Restore error: {e}")
                
    return redirect(url_for('audit'))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/compliance")
@login_required
def compliance():
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized: Requires Administrator or Compliance Officer privileges.", 403
    
    if db is None: return "Firestore not initialized", 500

    try:
        tickets_stream = db.collection('tickets').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        tickets = []
        
        users_cache = {}
        def get_username(uid):
            if not uid: return "N/A"
            uid_str = str(uid)
            if uid_str in users_cache: return users_cache[uid_str]
            doc = db.collection('users').document(uid_str).get()
            if doc.exists:
                name = doc.to_dict().get('username', 'Unknown')
                users_cache[uid_str] = name
                return name
            return "Unknown"

        for doc in tickets_stream:
            t_data = doc.to_dict()
            t_data['id'] = doc.id
            t_data['created_at'] = format_timestamp(t_data.get('created_at'))
            t_data['sender_name'] = get_username(str(t_data.get('user_id')))
            tickets.append(t_data)
    except Exception as e:
        print(f"Compliance error: {e}")
        tickets = []
    
    return render_template("compliance.html", tickets=tickets)

@app.route("/download_compliance")
@login_required
def download_compliance():
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
        
    if db is None: return "Firestore not initialized", 500

    try:
        tickets_stream = db.collection('tickets').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        tickets = []
        
        users_cache = {}
        def get_username(uid):
            if not uid: return "N/A"
            uid_str = str(uid)
            if uid_str in users_cache: return users_cache[uid_str]
            doc = db.collection('users').document(uid_str).get()
            if doc.exists:
                name = doc.to_dict().get('username', 'Unknown')
                users_cache[uid_str] = name
                return name
            return "Unknown"

        for doc in tickets_stream:
            t_data = doc.to_dict()
            t_data['id'] = doc.id
            t_data['created_at'] = format_timestamp(t_data.get('created_at'))
            t_data['sender_name'] = get_username(str(t_data.get('user_id')))
            tickets.append(t_data)
    except:
        tickets = []
    
    if not REPORTLAB_AVAILABLE:
        # Fallback to CSV if reportlab missing
        def generate():
            data = ["Ticket ID,Requester,Ticket Text,Category,Tone,Risk Level,Bias Flagged,Date"]
            for t in tickets:
                tx = str(t['ticket_text']).replace('"', '""').replace('\n', ' ')
                risk = str(t.get('risk_level', 'Low - Standard')).replace('"', '""').replace('\n', ' ')
                bias = str(t.get('bias_flag', 'No')).replace('"', '""').replace('\n', ' ')
                row = f'{t["id"]},"{t["sender_name"]}","{tx}","{t["category"]}","{t["tone"]}","{risk}","{bias}","{format_timestamp(t.get("created_at"))}"'
                data.append(row)
            yield '\n'.join(data) + '\n'
        return Response(generate(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=Governance_Evaluation.csv'})

    # Generate PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = styles['Heading1']
    elements.append(Paragraph("AI Governance & Risk Monitoring Report", title_style))
    elements.append(Spacer(1, 12))
    
    # Table Header
    data = [["ID", "Requester", "Ticket Text", "Dept", "Tone", "Risk Level", "Bias Flag", "Date"]]
    
    # Define a style for table content to allow wrapping
    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        wordWrap='CJK', # Good for long chunks
    )
    
    # Table Data
    for t in tickets:
        row_dict = dict(t)
        risk = row_dict.get('risk_level', 'Low')
        bias = row_dict.get('bias_flag', 'No')
        
        # Wrap long text fields in Paragraphs so they wrap instead of overlapping
        data.append([
            Paragraph(str(t['id']), table_cell_style),
            Paragraph(str(t['sender_name']), table_cell_style),
            Paragraph(str(t['ticket_text']), table_cell_style),
            Paragraph(str(t['category']), table_cell_style),
            Paragraph(str(t['tone']), table_cell_style),
            Paragraph(risk, table_cell_style),
            Paragraph(bias, table_cell_style),
            Paragraph(format_timestamp(t.get('created_at'))[:10], table_cell_style)
        ])
        
    # Optimized widths for landscape letter (approx 750pts wide)
    table = Table(data, colWidths=[30, 80, 200, 70, 70, 150, 90, 60], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#10b981')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 10),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8fafc')),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1'))
    ]))
    
    elements.append(table)
    doc.build(elements)
    
    buffer.seek(0)
    return send_file(
        buffer, 
        as_attachment=True, 
        download_name="Governance_Report.pdf", 
        mimetype='application/pdf'
    )

if __name__ == "__main__":
    import webbrowser
    from threading import Timer

    def open_browser():
        webbrowser.open_new("http://127.0.0.1:5000/")

    # Use a timer to wait for the server to initialize
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        Timer(1.5, open_browser).start()

    app.run(debug=True)
