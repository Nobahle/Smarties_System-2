from flask import Flask, render_template, request, session, redirect, url_for, jsonify, Response, send_file
import ast
import sqlite3
import string
import os
import io
import csv
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import requests
import json
from werkzeug.utils import secure_filename
from dotenv import load_dotenv


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
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'txt', 'log'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

DB_PATH = os.path.join(os.path.abspath(os.path.dirname(__file__)), "tickets.db")

# ---------------- DB ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT,
        role TEXT,
        department TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_text TEXT,
        category TEXT,
        tone TEXT,
        response TEXT,
        user_id INTEGER,
        assigned_to INTEGER,
        status TEXT DEFAULT 'Open',
        priority_level TEXT DEFAULT 'Normal',
        requires_approval INTEGER DEFAULT 0,
        is_approved INTEGER DEFAULT 0,
        risk_level TEXT DEFAULT 'Low - Standard Request',
        bias_flag TEXT DEFAULT 'No',
        transparency_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(assigned_to) REFERENCES users(id)
    )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            table_name TEXT,
            row_id INTEGER,
            old_value TEXT,
            new_value TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            performer TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            user_id INTEGER,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        type TEXT, -- e.g., 'Assignment', 'StatusUpdate', 'Approval'
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS automation_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT,
        target_department TEXT,
        target_priority TEXT,
        is_active INTEGER DEFAULT 1
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        user_id INTEGER,
        comment_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(ticket_id) REFERENCES tickets(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    
    # Check if we need to add new columns to existing tickets table
    try:
        cursor.execute("ALTER TABLE tickets ADD COLUMN assigned_to INTEGER")
    except: pass
    try:
        cursor.execute("ALTER TABLE tickets ADD COLUMN priority_level TEXT DEFAULT 'Normal'")
    except: pass
    try:
        cursor.execute("ALTER TABLE tickets ADD COLUMN attachment_path TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE tickets ADD COLUMN requires_approval INTEGER DEFAULT 0")
    except: pass
    try:
        cursor.execute("ALTER TABLE tickets ADD COLUMN is_approved INTEGER DEFAULT 0")
    except: pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN department TEXT")
    except: pass

    # Seed some default automation rules if table is empty
    cursor.execute("SELECT COUNT(*) FROM automation_rules")
    if cursor.fetchone()[0] == 0:
        rules = [
            ('salary', 'Finance', 'High'),
            ('urgent', 'IT', 'Emergency'),
            ('leave', 'HR', 'Normal'),
            ('invoice', 'Finance', 'Normal'),
            ('water', 'Operations', 'Normal')
        ]
        cursor.executemany("INSERT INTO automation_rules (keyword, target_department, target_priority) VALUES (?,?,?)", rules)

    conn.commit()
    conn.close()

init_db()

# ---------------- AUDIT ----------------
def log_action(action, table_name, record_id, old_value, new_value, performer=None):
    conn = get_db()
    cursor = conn.cursor()
    performed_by = performer if performer else session.get('username', 'Unknown')
    cursor.execute("""
    INSERT INTO audit_log (action, table_name, record_id, old_value, new_value, performed_by)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (action, table_name, record_id, str(old_value), str(new_value), performed_by))
    conn.commit()
    conn.close()

# ---------------- NOTIFICATIONS ----------------
def send_trigger_email(to_email, subject, message):
    """
    Sends a real email using SMTP if credentials exist in .env.
    Falls back to a console print if no credentials are found.
    """
    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    
    # Check if we have real credentials or just placeholders
    if sender_email and sender_password and "your-gmail" not in sender_email and "your-app-password" not in sender_password:
        try:
            msg = MIMEText(message)
            msg['Subject'] = subject
            msg['From'] = sender_email
            msg['To'] = to_email

            # Use Gmail SMTP
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
            server.quit()
            print(f"[SUCCESS] Email sent to {to_email}")
            return
        except Exception as e:
            print(f"[CRITICAL ERROR] SMTP Failed for {to_email}. Details: {e}")
            print("TIP: Ensure you are using a Gmail 'App Password', not your normal password.")
    else:
        print(f"\n[MOCK MODE] No real SMTP credentials found in .env.")
        print(f"Target: {to_email} | Subject: {subject}")
        print(f"To enable real emails, update .env with your Gmail and App Password.\n")

def create_notification(user_id, message, type="Update"):
    if not user_id:
        return
    conn = get_db()
    
    # Fetch user's email for the external trigger
    user = conn.execute("SELECT email, username FROM users WHERE id=?", (user_id,)).fetchone()
    
    conn.execute("INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)", (user_id, message, type))
    conn.commit()
    conn.close()
    
    # Week 7: Trigger the external email simulation
    if user and user['email']:
        subject = f"Smarties Notification: {type} for {user['username']}"
        send_trigger_email(user['email'], subject, message)

# ---------------- EMAIL HELPER ----------------
def send_email(to_email, subject, body):
    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    if not sender_email or not sender_password:
        print(f"\n[MOCK EMAIL SENT]\nTo: {to_email}\nSubject: {subject}\nBody: {body}\n")
        return False

    try:
        msg = MIMEText(body, 'html')
        msg['Subject'] = subject
        msg['From'] = f"Smarties System <{sender_email}>"
        msg['To'] = to_email

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send email to {to_email}: {e}")
        return False

# ---------------- AUTOMATION ENGINE ----------------
def run_automation_engine(ticket_id):
    """
    Week 7 Feature: Transform system into automation engine.
    Automate ticket routing and priority based on keywords and AI analysis.
    """
    conn = get_db()
    # Fetch ticket with user email and the AI response
    ticket = conn.execute("""
        SELECT t.*, u.email, u.username 
        FROM tickets t 
        JOIN users u ON t.user_id = u.id 
        WHERE t.id=?
    """, (ticket_id,)).fetchone()
    if not ticket:
        conn.close()
        return

    text = ticket['ticket_text'].lower()
    
    # 1. Automate Ticket Routing based on rules
    rules = conn.execute("SELECT * FROM automation_rules WHERE is_active=1").fetchall()
    auto_assigned_dept = None
    auto_priority = None

    for rule in rules:
        if rule['keyword'] in text:
            auto_assigned_dept = rule['target_department']
            auto_priority = rule['target_priority']
            break

    # 2. Trigger Approval Workflow for Critical Departments or Risk Flags
    requires_approval = 0
    
    # Check for sensitive departments
    is_sensitive_dept = any(dept in (auto_assigned_dept or ticket['category'] or "") for dept in ["Finance", "HR"])
    
    # Check for AI-detected risks
    is_high_risk = "High" in (ticket['risk_level'] or "")
    is_biased = (ticket['bias_flag'] == "Yes")
    
    if is_sensitive_dept or ticket['tone'] == 'Urgent' or is_high_risk or is_biased:
        requires_approval = 1

    # 3. Find target user to assign (Mock logic: assign to first person in that department)
    assigned_to = None
    if auto_assigned_dept:
        agent = conn.execute("SELECT id FROM users WHERE department=? LIMIT 1", (auto_assigned_dept,)).fetchone()
        if agent:
            assigned_to = agent['id']

    # Update ticket with automated values
    conn.execute("""
        UPDATE tickets 
        SET category=COALESCE(?, category), 
            priority_level=COALESCE(?, priority_level),
            assigned_to=COALESCE(?, assigned_to),
            requires_approval=?
        WHERE id=?
    """, (auto_assigned_dept, auto_priority, assigned_to, requires_approval, ticket_id))
    
    conn.commit()
    
    # 4. Trigger Email Notification (Mocked)
    if assigned_to:
        create_notification(assigned_to, f"New ticket #{ticket_id} automatically assigned to you: {ticket['ticket_text'][:50]}...", "Assignment")
    
    # Notify requester via Dashboard
    requester_msg = f"Your ticket #{ticket_id} has been processed and routed to {auto_assigned_dept or ticket['category']}."
    create_notification(ticket['user_id'], requester_msg, "Update")
    
    # Notify requester via Email
    email_subject = f"Smarties System: Ticket #{ticket_id} Received"
    email_body = f"""
    <html>
    <body style="font-family: sans-serif; color: #1e293b; line-height: 1.6;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px;">
            <h2 style="color: #2563eb;">Ticket Confirmation</h2>
            <p>Hi <strong>{ticket['username']}</strong>,</p>
            <p>Your ticket has been received and assigned to the <strong>{auto_assigned_dept or ticket['category']}</strong> department.</p>
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
    send_email(ticket['email'], email_subject, email_body)

    conn.close()

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
        api_key = os.getenv("OPENROUTER_API_KEY")
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
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=period_days)).strftime('%Y-%m-%d')
    prev_cutoff = (datetime.now() - timedelta(days=period_days * 2)).strftime('%Y-%m-%d')

    base_filter = "created_at >= ?"
    params = [cutoff]
    prev_filter = "created_at >= ? AND created_at < ?"
    prev_params = [prev_cutoff, cutoff]
    
    if department and department != "All":
        base_filter += " AND category LIKE ?"
        params.append(f"%{department}%")
        prev_filter += " AND category LIKE ?"
        prev_params.append(f"%{department}%")

    def q(sql, p): return conn.execute(sql, p).fetchone()[0]

    total       = q(f"SELECT COUNT(*) FROM tickets WHERE {base_filter}", params)
    prev_total  = q(f"SELECT COUNT(*) FROM tickets WHERE {prev_filter}", prev_params)
    resolved    = q(f"SELECT COUNT(*) FROM tickets WHERE {base_filter} AND status='Resolved'", params)
    in_progress = q(f"SELECT COUNT(*) FROM tickets WHERE {base_filter} AND status='In Progress'", params)
    open_t      = q(f"SELECT COUNT(*) FROM tickets WHERE {base_filter} AND status='Open'", params)
    users_count = q(f"SELECT COUNT(DISTINCT user_id) FROM tickets WHERE {base_filter}", params)

    dept_breakdown = {}
    surge_dept = None
    max_surge = 0

    for dept in DEPARTMENTS:
        p2 = [cutoff, f"%{dept}%"]
        p2_prev = [prev_cutoff, cutoff, f"%{dept}%"]

        cur_d_total = q("SELECT COUNT(*) FROM tickets WHERE created_at >= ? AND category LIKE ?", p2)
        prev_d_total = q("SELECT COUNT(*) FROM tickets WHERE created_at >= ? AND created_at < ? AND category LIKE ?", p2_prev)
        
        surge = cur_d_total - prev_d_total
        if surge > max_surge and surge > 0:
            max_surge = surge
            surge_dept = dept

        dept_breakdown[dept] = {
            "total":    cur_d_total,
            "resolved": q("SELECT COUNT(*) FROM tickets WHERE created_at >= ? AND category LIKE ? AND status='Resolved'", p2),
            "open":     q("SELECT COUNT(*) FROM tickets WHERE created_at >= ? AND category LIKE ? AND status='Open'", p2),
        }

    tone_data = {}
    for tone in ["Urgent", "Friendly", "Formal"]:
        p3 = params + [tone]
        tone_data[tone] = q(f"SELECT COUNT(*) FROM tickets WHERE {base_filter} AND tone=?", p3)

    recent = conn.execute(
        f"""
        SELECT t.id, t.ticket_text, t.category, t.tone, t.status, t.created_at, 
               t.response, t.priority_level, t.requires_approval, t.is_approved,
               a.username as agent_name
        FROM tickets t
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE t.{base_filter} 
        ORDER BY t.created_at DESC LIMIT 10
        """,
        params
    ).fetchall()

    conn.close()
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
        "closure_rate": closure_rate, "users_count": users_count,
        "dept_breakdown": dept_breakdown, "tone_data": tone_data,
        "recent_tickets": [dict(r) for r in recent],
        "period_days": period_days, "department": department or "All",
        "generated_at": datetime.now().strftime("%d %B %Y, %H:%M"),
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
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session['user_id'] = user["id"]
            session['username'] = user["username"]
            session['role'] = user["role"]
            # Log login action
            log_action("LOGIN", "users", user["id"], None, "User logged in", performer=user["username"])
            return redirect(url_for('dashboard'))
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

        conn = get_db()
        try:
            # Log registration action
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username,email,password,role) VALUES (?,?,?,?)",
                         (username, email, generate_password_hash(password), "user"))
            user_id = cursor.lastrowid
            conn.commit()
            log_action("INSERT", "users", user_id, None, "New user registered", performer=username)
            
            # Send welcome email
            subject = "Welcome to Smarties Ticket System!"
            message = f"Hello {username},\n\nYour account has been successfully created. You can now log in and submit tickets.\n\nBest regards,\nSmarties Team"
            send_trigger_email(email, subject, message)
        except Exception as e:
            print(f"Registration error: {e}")
            conn.close()
            return render_template("register.html", error="Username or Email already exists.")
        
        conn.close()
        return redirect(url_for('login', success="Account created successfully!"))
    return render_template("register.html")

@app.route("/dashboard")
@login_required
def dashboard():
    search = request.args.get('search', '')
    status_filter = request.args.get('status', '')
    category_filter = request.args.get('category', '')
    
    conn = get_db()
    query = """
        SELECT t.*, u.username as sender_name, a.username as agent_name
        FROM tickets t 
        JOIN users u ON t.user_id = u.id 
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE 1=1
    """
    params = []
    
    if search:
        query += " AND (t.ticket_text LIKE ? OR u.username LIKE ? OR t.id LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
    if status_filter:
        query += " AND t.status = ?"
        params.append(status_filter)
    if category_filter:
        query += " AND t.category LIKE ?"
        params.append(f'%{category_filter}%')
        
    query += " ORDER BY t.created_at DESC"
    tickets = conn.execute(query, params).fetchall()
    
    conn.close()
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
            # Create user-specific folder for better organization
            user_folder = os.path.join(app.config['UPLOAD_FOLDER'], f"user_{user_id}")
            if not os.path.exists(user_folder):
                os.makedirs(user_folder)
            
            filename = secure_filename(f"ticket_{int(datetime.now().timestamp())}_{file.filename}")
            file.save(os.path.join(user_folder, filename))
            # Store relative path for database access
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
        'user_id': session['user_id'],
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

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tickets (ticket_text, category, tone, response, user_id, status, risk_level, bias_flag, transparency_note, attachment_path) 
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (draft['ticket_text'], draft['category'], draft['tone'], draft['response'], draft['user_id'], "Open", draft.get('risk_level', 'Low - Standard'), draft.get('bias_flag', 'No'), draft.get('transparency_note', ''), draft.get('attachment_path')))
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    log_action("INSERT", "tickets", ticket_id, None, draft['ticket_text'])
    
    # Week 7 Integration: Run Automation Engine
    run_automation_engine(ticket_id)
    
    # Week 7: Notify all admins of global submission
    admins = get_db().execute("SELECT id FROM users WHERE role='admin'").fetchall()
    for admin in admins:
        create_notification(admin['id'], f"New ticket #{ticket_id} submitted by {session['username']}.", "Global")

    # Trigger the new premium success modal
    session['show_success_modal'] = True
    return redirect(url_for('dashboard'))

@app.route("/notifications")
@login_required
def notifications():
    conn = get_db()
    if session['role'] == 'admin':
        # Admins only see critical Global alerts (New Submissions & Deletions)
        notifications = conn.execute("""
            SELECT n.*, u.username as target_user 
            FROM notifications n
            JOIN users u ON n.user_id = u.id
            WHERE n.type IN ('Global', 'Chat')
            ORDER BY n.created_at DESC
        """).fetchall()
    else:
        notifications = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC", (session['user_id'],)).fetchall()
    
    # Mark as read
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    conn.commit()
    conn.close()
    return render_template("notifications.html", notifications=notifications)

@app.route("/approve_ticket/<int:ticket_id>", methods=["POST"])
@login_required
def approve_ticket(ticket_id):
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
        
    conn = get_db()
    ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if ticket:
        conn.execute("UPDATE tickets SET is_approved=1, status='In Progress' WHERE id=?", (ticket_id,))
        conn.commit()
        log_action("APPROVE", "tickets", ticket_id, "Pending Approval", "Approved")
        create_notification(ticket['user_id'], f"Your ticket #{ticket_id} has been approved and is now in progress.", "Approval")
    
    conn.close()
    return redirect(request.referrer or url_for('dashboard'))

@app.route("/update_assignment/<int:ticket_id>", methods=["POST"])
@login_required
def update_assignment(ticket_id):
    selected_depts = request.form.getlist("departments")
    if not selected_depts:
        selected_depts = ["Unrecognized"]
    
    category_str = ",".join(selected_depts)
    new_response = generate_response(selected_depts)
    
    conn = get_db()
    old_ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    
    if old_ticket:
        # Check permissions: owner or admin
        if session.get('role', '').lower() == 'admin' or old_ticket['user_id'] == session.get('user_id'):
            log_action("UPDATE", "tickets", ticket_id, dict(old_ticket), {"category": category_str, "response": new_response})
            conn.execute("UPDATE tickets SET category=?, response=? WHERE id=?", (category_str, new_response, ticket_id))
            conn.commit()
    
    conn.close()
    return redirect(url_for('dashboard'))

@app.route("/delete_ticket/<int:ticket_id>", methods=["POST"])
@login_required
def delete_ticket(ticket_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    ticket = cursor.fetchone()
    
    if not ticket:
        conn.close()
        return "Ticket not found", 404
        
    user_role = str(session.get('role', '')).lower()
    session_user_id = str(session.get('user_id', ''))
    ticket_owner_id = str(ticket['user_id'])
    
    if user_role == 'admin' or session_user_id == ticket_owner_id:
        log_action("DELETE", "tickets", ticket_id, dict(ticket), None)
        conn.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
        conn.commit()
        
        # Week 7: Notify all admins of deletion
        admins = conn.execute("SELECT id FROM users WHERE role='admin'").fetchall()
        for admin in admins:
            create_notification(admin['id'], f"Ticket #{ticket_id} was deleted by {session['username']}.", "Global")
    else:
        conn.close()
        return f"Unauthorized: User {session_user_id} cannot delete ticket owned by {ticket_owner_id}", 403
        
    conn.close()
    session['show_delete_modal'] = True
    return redirect(request.referrer or url_for('dashboard'))

@app.route("/update_status/<int:ticket_id>", methods=["POST"])
@login_required
def update_status(ticket_id):
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
    
    new_status = request.form.get("status")
    new_category = request.form.get("category")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    old = cursor.fetchone()
    
    if old:
        # Only update and log if something actually changed
        if new_status != old['status'] or (new_category and new_category != old['category']):
            cursor.execute("UPDATE tickets SET status=?, category=COALESCE(?, category) WHERE id=?", (new_status, new_category, ticket_id))
            conn.commit()
            log_action("UPDATE", "tickets", ticket_id, old['status'], new_status)
            
            # Notify user of changes
            msg = f"Ticket #{ticket_id} updated: Status is now '{new_status}'"
            if new_category and new_category != old['category']:
                msg += f" and routed to '{new_category}'"
            create_notification(old['user_id'], msg, "Update")
    
    conn.close()
    return redirect(url_for('dashboard'))

@app.route("/audit")
@login_required
def audit():
    conn = get_db()
    logs = conn.execute("SELECT * FROM audit_log ORDER BY timestamp DESC").fetchall()
    conn.close()
    return render_template("audit.html", logs=logs)

@app.route("/view")
@login_required
def view():
    search = request.args.get('search', '')
    status_filter = request.args.get('status', '')
    category_filter = request.args.get('category', '')

    conn = get_db()
    query = """
        SELECT t.*, u.username as sender_name, a.username as agent_name
        FROM tickets t 
        JOIN users u ON t.user_id = u.id 
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE 1=1
    """
    params = []
    
    if session.get('role') != 'admin':
        query += " AND t.user_id = ?"
        params.append(session.get('user_id'))
        
    if search:
        query += " AND (t.ticket_text LIKE ? OR t.id LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
    if status_filter:
        query += " AND t.status = ?"
        params.append(status_filter)
    if category_filter:
        query += " AND t.category LIKE ?"
        params.append(f'%{category_filter}%')

    query += " ORDER BY t.created_at DESC"
    tickets = conn.execute(query, params).fetchall()

    conn.close()
    return render_template("view.html", tickets=tickets, 
                           search=search, status_filter=status_filter, category_filter=category_filter)



@app.route("/chat/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def chat(ticket_id):
    conn = get_db()
    # Fetch ticket along with requester name
    ticket = conn.execute("""
        SELECT t.*, u.username as requester_name 
        FROM tickets t 
        JOIN users u ON t.user_id = u.id 
        WHERE t.id = ?
    """, (ticket_id,)).fetchone()
    
    if not ticket:
        conn.close()
        return "Ticket not found", 404
    
    # Permissions: Admin can see all, Users can only see their own tickets
    if session.get('role') != 'admin' and ticket['user_id'] != session.get('user_id'):
        conn.close()
        return "Unauthorized", 403
        
    if request.method == "POST":
        message_text = request.form.get("message")
        if message_text:
            conn.execute("INSERT INTO ticket_messages (ticket_id, user_id, message) VALUES (?, ?, ?)",
                         (ticket_id, session['user_id'], message_text))
            conn.commit()
            
            # Smart Notifications
            if session.get('role') == 'admin':
                # Notify the ticket owner
                create_notification(ticket['user_id'], f"Admin responded to your discussion on Ticket #{ticket_id}.", "Chat")
            else:
                # Notify all admins with the user's name for clarity
                user_name = session.get('username', 'User')
                admins = conn.execute("SELECT id FROM users WHERE role='admin'").fetchall()
                for admin in admins:
                    create_notification(admin['id'], f"User {user_name} responded to Ticket #{ticket_id} discussion.", "Chat")
                    
    messages = conn.execute("""
        SELECT m.*, u.username, u.role as user_role
        FROM ticket_messages m 
        JOIN users u ON m.user_id = u.id 
        WHERE m.ticket_id = ? 
        ORDER BY m.timestamp ASC
    """, (ticket_id,)).fetchall()
    
    conn.close()
    return render_template("chat.html", ticket=ticket, messages=messages)



@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
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
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (session['user_id'],)).fetchone()[0]
    conn.close()
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
    
    cutoff = (datetime.now() - timedelta(days=period_days)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    sql = "SELECT id, status, category, tone, priority_level, created_at, ticket_text FROM tickets WHERE created_at >= ?"
    params = [cutoff]
    if department != "All":
        sql += " AND category LIKE ?"
        params.append(f"%{department}%")
    sql += " ORDER BY created_at DESC"
    tickets = conn.execute(sql, params).fetchall()
    conn.close()

    for t in tickets:
        preview = t['ticket_text'][:80].replace('\n', ' ') + '...' if len(t['ticket_text']) > 80 else t['ticket_text']
        writer.writerow([t['id'], t['status'], t['category'], t['tone'], t['priority_level'], t['created_at'], preview])
    
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
            meta_text = f"<b>Category:</b> {t['category']}  |  <b>Priority:</b> {priority}  |  <b>Created:</b> {str(t['created_at'])[:16]}"
            
            issue_text = f"<font color='white'><b>Issue:</b><br/>{t['ticket_text']}</font>"
            
            assigned = t.get('agent_name') or 'Auto-Queue'
            status_detail_color = "#f59e0b" if (t.get('requires_approval') == 1 and t.get('is_approved') == 0) else "#10b981"
            status_detail = "Awaiting Approval" if (t.get('requires_approval') == 1 and t.get('is_approved') == 0) else "Automated Processing Complete"
            
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
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=? AND email=?", (username, email)).fetchone()
        conn.close()
        
        if user:
            session['reset_user_id'] = user['id']
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
        
        conn = get_db()
        # Fetch username and email before logging
        user = conn.execute("SELECT username, email FROM users WHERE id=?", (user_id,)).fetchone()
        username = user['username'] if user else "Unknown"
        email = user['email'] if user else None
        
        conn.execute("UPDATE users SET password=? WHERE id=?", (hashed_pw, user_id))
        conn.commit()
        conn.close()
        
        log_action("UPDATE", "users", user_id, "PASSWORD_RESET", "NEW_PASSWORD_SET", performer=f"{username} (Self-Reset)")
        
        # Send notification email
        if email:
            subject = "Smarties Security Alert: Password Changed"
            message = f"Hello {username},\n\nThis is a notification to confirm that your password for the Smarties Ticket System has been successfully changed.\n\nIf you did not make this change, please contact an administrator immediately.\n\nBest regards,\nSmarties Security Team"
            send_trigger_email(email, subject, message)
            
        return redirect(url_for('login', success="Password reset successful! Please log in."))
        
    return render_template("reset_password.html")

@app.route("/restore_ticket/<int:log_id>", methods=["POST"])
@login_required
def restore_ticket(log_id):
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
        
    conn = get_db()
    log = conn.execute("SELECT * FROM audit_log WHERE id=?", (log_id,)).fetchone()
    
    if log and log['action'] == 'DELETE' and log['table_name'] == 'tickets':
        try:
            # Parse the old_value string back to a dict
            data = ast.literal_eval(log['old_value'])
            
            # Insert back into tickets
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tickets (ticket_text, user_id, status, category, response, created_at, tone)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (data['ticket_text'], data['user_id'], data['status'], 
                  data['category'], data['response'], data['created_at'], data['tone']))
            
            new_id = cursor.lastrowid
            conn.commit()
            
            # Log the restoration
            log_action("RESTORE", "tickets", new_id, None, f"Restored from Log #{log_id}")
            
        except Exception as e:
            print(f"Restore error: {e}")
            
    conn.close()
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
    
    conn = get_db()
    try:
        tickets = conn.execute("""
            SELECT t.*, u.username as sender_name 
            FROM tickets t 
            JOIN users u ON t.user_id = u.id 
            ORDER BY t.created_at DESC
        """).fetchall()
    except Exception as e:
        tickets = []
    
    conn.close()
    return render_template("compliance.html", tickets=tickets)

@app.route("/download_compliance")
@login_required
def download_compliance():
    if session.get('role', '').lower() != 'admin':
        return "Unauthorized", 403
        
    conn = get_db()
    try:
        tickets = conn.execute("SELECT t.*, u.username as sender_name FROM tickets t JOIN users u ON t.user_id = u.id ORDER BY t.created_at DESC").fetchall()
    except:
        tickets = []
    conn.close()
    
    if not REPORTLAB_AVAILABLE:
        # Fallback to CSV if reportlab missing
        def generate():
            data = ["Ticket ID,Requester,Ticket Text,Category,Tone,Risk Level,Bias Flagged,Date"]
            for t in tickets:
                tx = str(t['ticket_text']).replace('"', '""').replace('\n', ' ')
                risk = str(t.get('risk_level', 'Low - Standard')).replace('"', '""').replace('\n', ' ')
                bias = str(t.get('bias_flag', 'No')).replace('"', '""').replace('\n', ' ')
                row = f'{t["id"]},"{t["sender_name"]}","{tx}","{t["category"]}","{t["tone"]}","{risk}","{bias}","{t["created_at"]}"'
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
            Paragraph(str(t['created_at'])[:10], table_cell_style)
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
