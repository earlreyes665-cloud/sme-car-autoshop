# SME Car Autoshop Management System

A Flask + MySQL application built from the supplied SME Car Autoshop HTML/CSS and Flask files. The included pages are wired to real database records rather than hardcoded demo rows.

## Modules

- Dashboard: live counts, low-stock count, paid revenue, recent job orders
- Customers: create, edit, search, and delete (deletion is blocked while vehicles are linked)
- Vehicles: create, edit, search, and delete (deletion is blocked when job history exists)
- Auto parts: stock quantity, price, low-stock status, create/edit/delete
- Services: pricing, estimated duration, active/inactive toggle, create/edit/delete
- Job orders: linked vehicle/customer, technician, status, active service selections, estimated service total
- Invoices: only completed jobs can be billed; one invoice per job; totals/tax calculated on the server; print/save as PDF
- System users: admin-only account management, role/status, hashed passwords
- Login/logout: session-based access control

## 1. Requirements

- Python 3.10 or newer
- XAMPP with MySQL/MariaDB running (or another MySQL-compatible server)
- VS Code or another editor

## 2. Create the database

1. Start **MySQL** in the XAMPP Control Panel.
2. Open `http://localhost/phpmyadmin`.
3. Select the **SQL** tab and run the contents of `database.sql`, or run:

   ```sql
   CREATE DATABASE IF NOT EXISTS sme_car_autoshop
   CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```

The application creates its tables on first start. It uses a new database named `sme_car_autoshop` so it will not destructively overwrite your previous `sme` database/tables.

## 3. Install dependencies

Open the extracted project folder in VS Code, open Terminal, and run:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, use Command Prompt:

```bat
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Configure MySQL (if needed)

Default configuration matches a typical local XAMPP install:
- Host: `127.0.0.1`
- Username: `root`
- Password: empty
- Database: `sme_car_autoshop`

If your MySQL account has a password or different details, set environment variables before running the app.

PowerShell example:

```powershell
$env:DB_HOST="127.0.0.1"
$env:DB_USER="root"
$env:DB_PASSWORD="your_mysql_password"
$env:DB_NAME="sme_car_autoshop"
$env:SECRET_KEY="replace-with-a-long-random-secret"
```

## 5. Run the system

```bash
python app.py
```

Open `http://127.0.0.1:5000`.

## First login

The first run creates a default administrator if the `users` table has no account with the configured admin username.

- Username: `admin`
- Password: `ChangeMe123!`

Change the default password after login. To choose a different first-run administrator, set these environment variables **before the first run**:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD` (at least 8 characters recommended)
- `ADMIN_FULL_NAME`
- `ADMIN_EMAIL`

The password is stored as a Werkzeug password hash, not plain text.

## Important notes

- The browser UI must be served by Flask at `http://127.0.0.1:5000`; do not open template files directly from File Explorer.
- Use the sidebar to move between modules. Forms submit to Flask routes, which validate and save data in MySQL.
- Create a customer first, then a vehicle, then a job order. Mark the job order **Completed** before creating its invoice.
- Service selections on a job order are stored as job-order service links. The invoice's parts amount is entered by the staff member; this version tracks inventory quantities but does not automatically deduct parts when invoicing because individual part usage per job has not been specified.
- This is a local learning/project baseline, not a security-audited production deployment. Before public deployment, configure a strong secret key, HTTPS, CSRF protection, backups, database least-privilege access, and proper operational logging.
- Existing tables in your older `sme` database are not automatically migrated. This project uses the separate `sme_car_autoshop` database to avoid destructive schema changes.
