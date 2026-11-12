import base64
import os
import re
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pymysql
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import URL
from sqlalchemy.exc import IntegrityError

load_dotenv()
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps

app = Flask(__name__)
is_render = bool(os.environ.get("RENDER"))
secret_key = os.environ.get("SECRET_KEY")
if is_render and not secret_key:
    raise RuntimeError("Set a strong SECRET_KEY in the Render environment.")
app.config["SECRET_KEY"] = secret_key or "local-development-only-change-before-deployment"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = is_render

# TiDB Cloud settings take priority; DB_* variables remain supported for local MySQL.
db_user = os.environ.get("TIDB_USER") or os.environ.get("DB_USER", "root")
db_password = os.environ.get("TIDB_PASSWORD") or os.environ.get("DB_PASSWORD", "")
db_host = os.environ.get("TIDB_HOST") or os.environ.get("DB_HOST", "127.0.0.1")
db_port = int(os.environ.get("TIDB_PORT") or ("4000" if os.environ.get("TIDB_HOST") else "3306"))
db_name = os.environ.get("TIDB_DATABASE") or os.environ.get("DB_NAME", "sme_car_autoshop")
ca_path = os.environ.get("TIDB_CA_PATH") or os.environ.get("CA_PATH")
ca_cert_b64 = os.environ.get("TIDB_CA_CERT_BASE64")
if ca_cert_b64:
    try:
        cert_bytes = base64.b64decode(ca_cert_b64, validate=True)
        cert_file = os.path.join(tempfile.gettempdir(), "tidb-cloud-ca.pem")
        with open(cert_file, "wb") as cert_handle:
            cert_handle.write(cert_bytes)
        ca_path = cert_file
    except Exception as exc:
        raise RuntimeError("TIDB_CA_CERT_BASE64 must contain the Base64-encoded TiDB CA certificate.") from exc

if not re.fullmatch(r"[A-Za-z0-9_]+", db_name):
    raise RuntimeError("TIDB_DATABASE/DB_NAME may contain only letters, numbers, and underscores.")

ssl_options = None
if os.environ.get("TIDB_HOST"):
    # Render's Linux image provides the system CA bundle. Locally, point TIDB_CA_PATH
    # to the downloaded TiDB CA certificate (for example: certs/ca.pem).
    ssl_options = {"check_hostname": True}
    if ca_path:
        ssl_options["ca"] = ca_path

database_url = URL.create(
    "mysql+pymysql",
    username=db_user,
    password=db_password,
    host=db_host,
    port=db_port,
    database=db_name,
    query={"charset": "utf8mb4"},
)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "pool_size": 5,
    "max_overflow": 2,
    "connect_args": {"connect_timeout": 15},
}
if ssl_options:
    engine_options["connect_args"]["ssl"] = ssl_options
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_options
db = SQLAlchemy(app)

# ----------------------------- DATABASE MODELS -----------------------------

class SystemUser(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    role = db.Column(db.String(30), nullable=False, default="Service Advisor")
    status = db.Column(db.String(20), nullable=False, default="Active")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Customer(db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    phone = db.Column(db.String(25), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    customer_type = db.Column(db.String(30), nullable=False, default="Regular")
    address = db.Column(db.String(255), nullable=True)
    vehicles = db.relationship("Vehicle", back_populates="owner", lazy=True)
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

class Vehicle(db.Model):
    __tablename__ = "vehicles"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    plate_number = db.Column(db.String(20), unique=True, nullable=False)
    brand = db.Column(db.String(60), nullable=False)
    model = db.Column(db.String(60), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    color = db.Column(db.String(40), nullable=True)
    chassis_no = db.Column(db.String(100), nullable=True)
    owner = db.relationship("Customer", back_populates="vehicles")
    job_orders = db.relationship("JobOrder", back_populates="vehicle", lazy=True)
    @property
    def display_name(self):
        return f"{self.brand} {self.model} ({self.plate_number})"

class AutoPart(db.Model):
    __tablename__ = "auto_parts"
    id = db.Column(db.Integer, primary_key=True)
    part_name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(60), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    min_stock = db.Column(db.Integer, nullable=False, default=5)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    @property
    def stock_status(self):
        if self.quantity <= 0:
            return "Out of Stock"
        if self.quantity <= self.min_stock:
            return "Low Stock"
        return "In Stock"

class Service(db.Model):
    __tablename__ = "services"
    id = db.Column(db.Integer, primary_key=True)
    service_name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(60), nullable=False)
    base_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    est_time = db.Column(db.Numeric(5, 2), nullable=False, default=1)
    description = db.Column(db.Text, nullable=True)
    availability = db.Column(db.String(20), nullable=False, default="Active")

class JobOrder(db.Model):
    __tablename__ = "job_orders"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    technician_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="Pending")
    complaint = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    vehicle = db.relationship("Vehicle", back_populates="job_orders")
    technician = db.relationship("SystemUser", foreign_keys=[technician_id])
    invoice = db.relationship("Invoice", back_populates="job_order", uselist=False)
    @property
    def total_cost(self):
        return sum((Decimal(str(line.service.base_price)) for line in self.service_lines if line.service), Decimal("0.00"))
    service_lines = db.relationship("JobOrderService", back_populates="job_order", cascade="all, delete-orphan", lazy=True)

class JobOrderService(db.Model):
    __tablename__ = "job_order_services"
    id = db.Column(db.Integer, primary_key=True)
    job_order_id = db.Column(db.Integer, db.ForeignKey("job_orders.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)
    service = db.relationship("Service")
    job_order = db.relationship("JobOrder", back_populates="service_lines")

class Invoice(db.Model):
    __tablename__ = "invoices"
    id = db.Column(db.Integer, primary_key=True)
    job_order_id = db.Column(db.Integer, db.ForeignKey("job_orders.id"), unique=True, nullable=False)
    labor_fee = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    parts_fee = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    discount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    tax_percent = db.Column(db.Numeric(5, 2), nullable=False, default=12)
    payment_method = db.Column(db.String(30), nullable=False, default="Cash")
    payment_status = db.Column(db.String(20), nullable=False, default="Pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    job_order = db.relationship("JobOrder", back_populates="invoice")
    @property
    def subtotal(self):
        return Decimal(str(self.labor_fee)) + Decimal(str(self.parts_fee))
    @property
    def tax_amount(self):
        taxable = max(Decimal("0.00"), self.subtotal - Decimal(str(self.discount)))
        return (taxable * Decimal(str(self.tax_percent)) / Decimal("100")).quantize(Decimal("0.01"))
    @property
    def total_amount(self):
        return max(Decimal("0.00"), self.subtotal - Decimal(str(self.discount))) + self.tax_amount

# ----------------------------- HELPERS -------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get("role") != "Admin":
            flash("Administrator permission is required for that action.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped

def money(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")

@app.context_processor
def inject_globals():
    return {"current_user": session, "now": datetime.now}

@app.template_filter("peso")
def peso(value):
    return f"₱{Decimal(str(value or 0)):,.2f}"

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = SystemUser.query.filter_by(username=username).first()
        valid = False
        if user:
            # Compatibility with older uploaded app.py databases storing plaintext passwords.
            if user.password_hash.startswith(("pbkdf2:", "scrypt:")):
                valid = check_password_hash(user.password_hash, password)
            else:
                valid = user.password_hash == password
                if valid:
                    user.password_hash = generate_password_hash(password)
                    db.session.commit()
        if user and valid and user.status == "Active":
            session.clear()
            session["user_id"] = user.id
            session["full_name"] = user.full_name
            session["username"] = user.username
            session["email"] = user.email
            session["role"] = user.role
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid credentials or inactive account.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    recent_jobs = JobOrder.query.order_by(JobOrder.created_at.desc()).limit(8).all()
    return render_template(
        "index.html",
        customers_count=Customer.query.count(),
        vehicles_count=Vehicle.query.count(),
        active_jobs=JobOrder.query.filter(JobOrder.status.in_(["Pending", "In Progress"])).count(),
        low_stock=AutoPart.query.filter(AutoPart.quantity <= AutoPart.min_stock).count(),
        revenue=sum((inv.total_amount for inv in Invoice.query.filter_by(payment_status="Paid").all()), Decimal("0.00")),
        recent_jobs=recent_jobs,
    )

# Customers
@app.route("/customer", methods=["GET", "POST"])
@login_required
def customers():
    edit_id = request.args.get("edit", type=int)
    editing = db.session.get(Customer, edit_id) if edit_id else None
    if request.method == "POST":
        item_id = request.form.get("id", type=int)
        item = db.session.get(Customer, item_id) if item_id else Customer()
        if item_id and not item:
            flash("Customer record was not found.", "error")
            return redirect(url_for("customers"))
        item.first_name = request.form.get("first_name", "").strip()
        item.last_name = request.form.get("last_name", "").strip()
        item.phone = request.form.get("phone", "").strip()
        item.email = request.form.get("email", "").strip() or None
        item.customer_type = request.form.get("customer_type", "Regular")
        item.address = request.form.get("address", "").strip() or None
        if not item.first_name or not item.last_name or not item.phone:
            flash("First name, last name and phone are required.", "error")
        else:
            try:
                if not item_id:
                    db.session.add(item)
                db.session.commit()
                flash("Customer saved successfully.", "success")
                return redirect(url_for("customers"))
            except IntegrityError:
                db.session.rollback()
                flash("That email is already used by another customer.", "error")
    items = Customer.query.order_by(Customer.last_name, Customer.first_name).all()
    return render_template("customer.html", items=items, editing=editing)

@app.post("/customer/<int:item_id>/delete")
@login_required
def customer_delete(item_id):
    item = db.session.get(Customer, item_id)
    if not item:
        flash("Customer not found.", "error")
    elif item.vehicles or Vehicle.query.filter_by(customer_id=item.id).first():
        flash("This customer has linked vehicles. Remove or reassign those vehicles first.", "error")
    else:
        db.session.delete(item); db.session.commit(); flash("Customer deleted.", "success")
    return redirect(url_for("customers"))

# Vehicles
@app.route("/vehicle", methods=["GET", "POST"])
@login_required
def vehicles():
    edit_id = request.args.get("edit", type=int)
    editing = db.session.get(Vehicle, edit_id) if edit_id else None
    if request.method == "POST":
        item_id = request.form.get("id", type=int)
        item = db.session.get(Vehicle, item_id) if item_id else Vehicle()
        try:
            item.customer_id = int(request.form.get("customer_id", ""))
            item.plate_number = request.form.get("plate_number", "").strip().upper()
            item.brand = request.form.get("brand", "").strip()
            item.model = request.form.get("model", "").strip()
            item.year = int(request.form.get("year", ""))
            item.color = request.form.get("color", "").strip() or None
            item.chassis_no = request.form.get("chassis_no", "").strip() or None
            if not item.plate_number or not item.brand or not item.model or item.year < 1900 or item.year > datetime.now().year + 1:
                raise ValueError
            if not item_id: db.session.add(item)
            db.session.commit()
            flash("Vehicle saved successfully.", "success")
            return redirect(url_for("vehicles"))
        except (ValueError, TypeError):
            db.session.rollback(); flash("Enter a valid owner, plate, make, model and year.", "error")
        except IntegrityError:
            db.session.rollback(); flash("That plate number is already registered.", "error")
    return render_template("vehicle.html", items=Vehicle.query.order_by(Vehicle.id.desc()).all(),
                           customers=Customer.query.order_by(Customer.last_name).all(), editing=editing)

@app.post("/vehicle/<int:item_id>/delete")
@login_required
def vehicle_delete(item_id):
    item = db.session.get(Vehicle, item_id)
    if not item: flash("Vehicle not found.", "error")
    elif item.job_orders: flash("This vehicle has job orders. Keep it for service history.", "error")
    else:
        db.session.delete(item); db.session.commit(); flash("Vehicle deleted.", "success")
    return redirect(url_for("vehicles"))

# Auto parts
@app.route("/autoparts", methods=["GET", "POST"])
@login_required
def autoparts():
    edit_id = request.args.get("edit", type=int)
    editing = db.session.get(AutoPart, edit_id) if edit_id else None
    if request.method == "POST":
        item_id = request.form.get("id", type=int)
        item = db.session.get(AutoPart, item_id) if item_id else AutoPart()
        try:
            item.part_name = request.form.get("part_name", "").strip()
            item.category = request.form.get("category", "").strip()
            item.quantity = int(request.form.get("quantity", "0"))
            item.unit_price = money(request.form.get("unit_price"))
            item.min_stock = int(request.form.get("min_stock", "5"))
            if not item.part_name or not item.category or item.quantity < 0 or item.unit_price < 0 or item.min_stock < 0:
                raise ValueError
            if not item_id: db.session.add(item)
            db.session.commit(); flash("Auto part saved successfully.", "success")
            return redirect(url_for("autoparts"))
        except (ValueError, TypeError, InvalidOperation):
            db.session.rollback(); flash("Enter valid part details and non-negative amounts.", "error")
    return render_template("autoparts.html", items=AutoPart.query.order_by(AutoPart.part_name).all(), editing=editing)

@app.post("/autoparts/<int:item_id>/delete")
@login_required
def autoparts_delete(item_id):
    item = db.session.get(AutoPart, item_id)
    if item:
        db.session.delete(item); db.session.commit(); flash("Auto part deleted.", "success")
    else: flash("Auto part not found.", "error")
    return redirect(url_for("autoparts"))

# Services
@app.route("/services", methods=["GET", "POST"])
@login_required
def services():
    edit_id = request.args.get("edit", type=int)
    editing = db.session.get(Service, edit_id) if edit_id else None
    if request.method == "POST":
        item_id = request.form.get("id", type=int)
        item = db.session.get(Service, item_id) if item_id else Service()
        try:
            item.service_name = request.form.get("service_name", "").strip()
            item.category = request.form.get("category", "").strip()
            item.base_price = money(request.form.get("base_price"))
            item.est_time = money(request.form.get("est_time"))
            item.description = request.form.get("description", "").strip() or None
            item.availability = request.form.get("availability", "Active")
            if not item.service_name or not item.category or item.base_price < 0 or item.est_time <= 0:
                raise ValueError
            if not item_id: db.session.add(item)
            db.session.commit(); flash("Service saved successfully.", "success")
            return redirect(url_for("services"))
        except (ValueError, TypeError, InvalidOperation):
            db.session.rollback(); flash("Enter valid service details.", "error")
    return render_template("services.html", items=Service.query.order_by(Service.service_name).all(), editing=editing)

@app.post("/services/<int:item_id>/toggle")
@login_required
def service_toggle(item_id):
    item = db.session.get(Service, item_id)
    if item:
        item.availability = "Inactive" if item.availability == "Active" else "Active"
        db.session.commit(); flash("Service availability updated.", "success")
    return redirect(url_for("services"))

@app.post("/services/<int:item_id>/delete")
@login_required
def service_delete(item_id):
    item = db.session.get(Service, item_id)
    if item and JobOrderService.query.filter_by(service_id=item_id).first():
        flash("This service is used in job history. Mark it inactive instead.", "error")
    elif item:
        db.session.delete(item); db.session.commit(); flash("Service deleted.", "success")
    else: flash("Service not found.", "error")
    return redirect(url_for("services"))

# Job orders, linked to vehicles, technicians and services
@app.route("/joborder", methods=["GET", "POST"])
@login_required
def job_orders():
    edit_id = request.args.get("edit", type=int)
    editing = db.session.get(JobOrder, edit_id) if edit_id else None
    active_services = Service.query.filter_by(availability="Active").order_by(Service.service_name).all()
    technicians = SystemUser.query.filter_by(status="Active").filter(SystemUser.role.in_(["Admin", "Technician", "Service Advisor"])).all()
    if request.method == "POST":
        item_id = request.form.get("id", type=int)
        item = db.session.get(JobOrder, item_id) if item_id else JobOrder()
        try:
            item.vehicle_id = int(request.form.get("vehicle_id", ""))
            tech = request.form.get("technician_id", "").strip()
            item.technician_id = int(tech) if tech else None
            item.status = request.form.get("status", "Pending")
            item.complaint = request.form.get("complaint", "").strip()
            service_ids = list(dict.fromkeys(int(x) for x in request.form.getlist("service_ids") if x.isdigit()))
            if item.status not in ["Pending", "In Progress", "Completed", "Cancelled"] or not item.complaint:
                raise ValueError
            selected = Service.query.filter(Service.id.in_(service_ids), Service.availability == "Active").all() if service_ids else []
            if not item_id: db.session.add(item)
            db.session.flush()
            item.service_lines.clear()
            for service in selected:
                item.service_lines.append(JobOrderService(service=service))
            if item.status == "Completed" and not item.completed_at: item.completed_at = datetime.utcnow()
            elif item.status != "Completed": item.completed_at = None
            db.session.commit(); flash("Job order saved and linked to its vehicle/services.", "success")
            return redirect(url_for("job_orders"))
        except (ValueError, TypeError):
            db.session.rollback(); flash("Select a valid vehicle and enter a complaint.", "error")
        except IntegrityError:
            db.session.rollback(); flash("Could not save this job order. Check the selected records.", "error")
    return render_template("joborder.html", items=JobOrder.query.order_by(JobOrder.created_at.desc()).all(),
                           vehicles=Vehicle.query.order_by(Vehicle.plate_number).all(), technicians=technicians,
                           services=active_services, editing=editing)

@app.post("/joborder/<int:item_id>/delete")
@login_required
def job_order_delete(item_id):
    item = db.session.get(JobOrder, item_id)
    if not item: flash("Job order not found.", "error")
    elif item.invoice: flash("This job order has an invoice; delete the invoice first.", "error")
    else:
        db.session.delete(item); db.session.commit(); flash("Job order deleted.", "success")
    return redirect(url_for("job_orders"))

# Invoices
@app.route("/invoice", methods=["GET", "POST"])
@login_required
def invoices():
    edit_id = request.args.get("edit", type=int)
    editing = db.session.get(Invoice, edit_id) if edit_id else None
    available_jobs = JobOrder.query.filter(JobOrder.status == "Completed").filter(~JobOrder.invoice.has()).order_by(JobOrder.created_at.desc()).all()
    if request.method == "POST":
        item_id = request.form.get("id", type=int)
        job_id = request.form.get("job_order_id", type=int)
        existing = db.session.get(Invoice, item_id) if item_id else None
        try:
            job = db.session.get(JobOrder, job_id)
            if not job or job.status != "Completed": raise ValueError
            duplicate = Invoice.query.filter_by(job_order_id=job_id).first()
            if duplicate and (not existing or duplicate.id != existing.id):
                flash("That completed job order already has an invoice.", "error")
                return redirect(url_for("invoices"))
            item = existing or Invoice()
            item.job_order_id = job_id
            item.labor_fee = money(request.form.get("labor_fee"))
            item.parts_fee = money(request.form.get("parts_fee"))
            item.discount = money(request.form.get("discount"))
            item.tax_percent = money(request.form.get("tax_percent"))
            item.payment_method = request.form.get("payment_method", "Cash")
            item.payment_status = request.form.get("payment_status", "Pending")
            if min(item.labor_fee, item.parts_fee, item.discount, item.tax_percent) < 0 or item.tax_percent > 100:
                raise ValueError
            if not existing: db.session.add(item)
            db.session.commit(); flash("Invoice saved successfully.", "success")
            return redirect(url_for("invoices"))
        except (ValueError, TypeError, InvalidOperation):
            db.session.rollback(); flash("Select a completed job order and enter valid billing amounts.", "error")
        except IntegrityError:
            db.session.rollback(); flash("An invoice already exists for that job order.", "error")
    return render_template("invoice.html", items=Invoice.query.order_by(Invoice.created_at.desc()).all(),
                           jobs=available_jobs, editing=editing)

@app.post("/invoice/<int:item_id>/delete")
@login_required
def invoice_delete(item_id):
    item = db.session.get(Invoice, item_id)
    if item: db.session.delete(item); db.session.commit(); flash("Invoice deleted.", "success")
    else: flash("Invoice not found.", "error")
    return redirect(url_for("invoices"))

@app.route("/invoice/<int:item_id>/print")
@login_required
def invoice_print(item_id):
    item = db.session.get(Invoice, item_id)
    if not item:
        flash("Invoice not found.", "error")
        return redirect(url_for("invoices"))
    return render_template("invoice_print.html", invoice=item)

# System users (admin only)
@app.route("/systemusers", methods=["GET", "POST"])
@admin_required
def system_users():
    edit_id = request.args.get("edit", type=int)
    editing = db.session.get(SystemUser, edit_id) if edit_id else None
    if request.method == "POST":
        item_id = request.form.get("id", type=int)
        item = db.session.get(SystemUser, item_id) if item_id else SystemUser()
        password = request.form.get("password", "")
        try:
            item.full_name = request.form.get("full_name", "").strip()
            item.username = request.form.get("username", "").strip()
            item.email = request.form.get("email", "").strip()
            item.role = request.form.get("role", "Service Advisor")
            item.status = request.form.get("status", "Active")
            if not item.full_name or not item.username or not item.email:
                raise ValueError
            if not item_id and len(password) < 8:
                raise ValueError
            if password:
                item.password_hash = generate_password_hash(password)
            elif not item_id:
                raise ValueError
            if item.role not in ["Admin", "Technician", "Service Advisor"] or item.status not in ["Active", "Inactive"]:
                raise ValueError
            if not item_id: db.session.add(item)
            db.session.commit(); flash("System user saved successfully.", "success")
            return redirect(url_for("system_users"))
        except (ValueError, TypeError):
            db.session.rollback(); flash("Use a valid name, username, email, role, status and password (at least 8 characters for new users).", "error")
        except IntegrityError:
            db.session.rollback(); flash("Username or email is already registered.", "error")
    return render_template("systemusers.html", items=SystemUser.query.order_by(SystemUser.full_name).all(), editing=editing)

@app.post("/systemusers/<int:item_id>/delete")
@admin_required
def system_user_delete(item_id):
    item = db.session.get(SystemUser, item_id)
    if not item: flash("User not found.", "error")
    elif item.id == session.get("user_id"): flash("You cannot delete your own logged-in account.", "error")
    elif JobOrder.query.filter_by(technician_id=item_id).first():
        flash("This user is assigned to job orders. Keep the account and mark it inactive.", "error")
    else:
        db.session.delete(item); db.session.commit(); flash("System user deleted.", "success")
    return redirect(url_for("system_users"))

@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="The page or record you requested was not found."), 404

@app.errorhandler(500)
def server_error(_error):
    db.session.rollback()
    return render_template("error.html", code=500, message="An unexpected server error occurred. Check the terminal for details."), 500

def initialize_database():
    # Create the application schema before SQLAlchemy connects to it on first deploy.
    if os.environ.get("TIDB_HOST"):
        if not db_password:
            raise RuntimeError("Set TIDB_PASSWORD in the hosting environment.")
        connection = pymysql.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database="sys",
            charset="utf8mb4",
            connect_timeout=15,
            ssl=ssl_options,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                )
        finally:
            connection.close()

    with app.app_context():
        db.create_all()
        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin = SystemUser.query.filter_by(username=admin_username).first()
        if not admin:
            admin_password = os.environ.get("ADMIN_PASSWORD")
            if is_render and (not admin_password or len(admin_password) < 12):
                raise RuntimeError("Set ADMIN_PASSWORD to a strong password of at least 12 characters before first deployment.")
            admin = SystemUser(
                full_name=os.environ.get("ADMIN_FULL_NAME", "System Administrator"),
                username=admin_username,
                email=os.environ.get("ADMIN_EMAIL", "admin@autoshop.local"),
                password_hash=generate_password_hash(admin_password or "ChangeMe123!"),
                role="Admin", status="Active"
            )
            db.session.add(admin)
            db.session.commit()
            print("Initial administrator account created. Change its password after first login.")
if __name__ == "__main__":
    initialize_database()
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1", port=int(os.environ.get("PORT", "5000")))
