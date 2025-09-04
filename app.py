import os
from datetime import datetime
from io import BytesIO
import csv
from functools import wraps

from flask import (
    Flask, render_template_string, request, redirect,
    url_for, flash, send_file
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user, UserMixin
)
from werkzeug.security import generate_password_hash, check_password_hash

# -------------------- Flask & DB setup --------------------
app = Flask(__name__)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me-now')
# file-based sqlite in current folder; override with env if you want
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# -------------------- Models --------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='supervisor')

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

class Person(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    classification = db.Column(db.String(50), nullable=False)  # Direto / Indireto

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(200), nullable=False)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True, nullable=False)  # 5 digits
    client = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(200), nullable=False)

class Hour(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    person_id = db.Column(db.Integer, nullable=False)
    team_id = db.Column(db.Integer, nullable=False)
    project_id = db.Column(db.Integer, nullable=False)
    entry = db.Column(db.String(5), nullable=False)  # HH:MM
    exit = db.Column(db.String(5), nullable=False)   # HH:MM
    worked_hours = db.Column(db.Float, nullable=False)

# -------------------- Auth helpers --------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def supervisor_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'supervisor':
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapped

# -------------------- Bootstrap DB with admin --------------------
@app.before_first_request
def init_db_and_admin():
    db.create_all()
    # create default admin from env or fallback admin/admin
    admin_user = os.getenv('ADMIN_USERNAME', 'admin')
    admin_pass = os.getenv('ADMIN_PASSWORD', 'admin')
    u = User.query.filter_by(username=admin_user).first()
    if not u:
        u = User(username=admin_user, role='supervisor')
        u.set_password(admin_pass)
        db.session.add(u)
        db.session.commit()

# -------------------- Routes --------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username', '').strip()).first()
        if u and u.check_password(request.form.get('password', '')):
            login_user(u)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@supervisor_required
def dashboard():
    # Create handlers (simple & explicit)
    if request.method == 'POST':
        kind = request.form.get('kind')

        try:
            if kind == 'person':
                name = request.form['name'].strip()
                cls = request.form['class'].strip()
                if not name: raise ValueError('Name required')
                db.session.add(Person(name=name, classification=cls))

            elif kind == 'team':
                code = request.form['code'].strip()
                desc = request.form['desc'].strip()
                if not code or not desc: raise ValueError('Code and description required')
                db.session.add(Team(code=code, description=desc))

            elif kind == 'project':
                number = int(request.form['number'])
                if number < 10000 or number > 99999:
                    raise ValueError('Project number must be 5 digits')
                client = request.form['client'].strip()
                desc = request.form['desc'].strip()
                db.session.add(Project(number=number, client=client, description=desc))

            elif kind == 'hour':
                date = request.form['date']
                person_id = int(request.form['person_id'])
                team_id = int(request.form['team_id'])
                project_id = int(request.form['project_id'])
                entry = request.form['entry']
                exit_ = request.form['exit']

                # server-side compute worked hours
                def to_minutes(hhmm):
                    hh, mm = map(int, hhmm.split(':'))
                    return hh*60 + mm

                start = to_minutes(entry)
                end = to_minutes(exit_)
                if end <= start:
                    raise ValueError('Exit must be after entry')

                worked = (end - start) / 60.0  # no lunch deduction; add rule if needed
                db.session.add(Hour(
                    date=date, person_id=person_id, team_id=team_id, project_id=project_id,
                    entry=entry, exit=exit_, worked_hours=round(worked, 2)
                ))
            else:
                raise ValueError('Unknown form submission')

            db.session.commit()
            return redirect(url_for('dashboard'))

        except Exception as e:
            db.session.rollback()
            flash(str(e), 'danger')

    # Read sets for dropdowns
    people = Person.query.order_by(Person.name.asc()).all()
    teams = Team.query.order_by(Team.code.asc()).all()
    projects = Project.query.order_by(Project.number.asc()).all()
    hours = Hour.query.order_by(Hour.date.desc(), Hour.id.desc()).all()

    return render_template_string(
        DASHBOARD_HTML,
        people=people, teams=teams, projects=projects, hours=hours
    )

@app.route('/export')
@supervisor_required
def export_csv():
    # Create CSV in memory (Excel-friendly with BOM)
    output = BytesIO()
    writer = csv.writer(output := BytesIO(), delimiter=',')
    # header
    writer.writerow(['Date', 'Person', 'Team', 'Project', 'Entry', 'Exit', 'WorkedHours'])
    # body
    for h in Hour.query.order_by(Hour.date.asc(), Hour.id.asc()).all():
        writer.writerow([
            h.date,
            getattr(Person.query.get(h.person_id), 'name', h.person_id),
            getattr(Team.query.get(h.team_id), 'code', h.team_id),
            getattr(Project.query.get(h.project_id), 'number', h.project_id),
            h.entry, h.exit, f'{h.worked_hours:.2f}'
        ])
    data = output.getvalue()
    return send_file(
        BytesIO(data),
        mimetype='text/csv',
        as_attachment=True,
        download_name='hours.csv'
    )

# -------------------- Inline templates --------------------
LOGIN_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Supervisor Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  </head>
  <body class="bg-light">
    <div class="container py-5">
      <div class="row justify-content-center">
        <div class="col-md-4">
          <div class="card shadow-sm">
            <div class="card-body">
              <h5 class="mb-3">Supervisor Login</h5>
              {% with messages = get_flashed_messages(with_categories=true) %}
                {% for cat, msg in messages %}
                  <div class="alert alert-{{cat}} py-2">{{ msg }}</div>
                {% endfor %}
              {% endwith %}
              <form method="post">
                <div class="mb-3">
                  <label class="form-label">Username</label>
                  <input name="username" class="form-control" autocomplete="username" required>
                </div>
                <div class="mb-3">
                  <label class="form-label">Password</label>
                  <input type="password" name="password" class="form-control" autocomplete="current-password" required>
                </div>
                <button class="btn btn-primary w-100">Login</button>
              </form>
            </div>
          </div>
          <div class="text-muted small mt-3">Default admin comes from env <code>ADMIN_USERNAME</code>/<code>ADMIN_PASSWORD</code> (fallback admin/admin).</div>
        </div>
      </div>
    </div>
  </body>
</html>
"""

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Timesheet Dashboard</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3 class="mb-0">Timesheet Dashboard</h3>
    <a class="btn btn-outline-danger" href="{{ url_for('logout') }}">Logout</a>
  </div>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in messages %}
      <div class="alert alert-{{cat}} py-2">{{ msg }}</div>
    {% endfor %}
  {% endwith %}

  <div class="row g-3">
    <div class="col-md-4">
      <div class="card shadow-sm">
        <div class="card-body">
          <h6 class="mb-3">Add Person</h6>
          <form method="post">
            <input type="hidden" name="kind" value="person">
            <div class="mb-2">
              <label class="form-label">Name</label>
              <input name="name" class="form-control" required>
            </div>
            <div class="mb-3">
              <label class="form-label">Classification</label>
              <select name="class" class="form-select" required>
                <option value="Direto">Direto</option>
                <option value="Indireto">Indireto</option>
              </select>
            </div>
            <button class="btn btn-success w-100">Add</button>
          </form>
        </div>
      </div>
    </div>

    <div class="col-md-4">
      <div class="card shadow-sm">
        <div class="card-body">
          <h6 class="mb-3">Add Team</h6>
          <form method="post">
            <input type="hidden" name="kind" value="team">
            <div class="mb-2">
              <label class="form-label">Code</label>
              <input name="code" class="form-control" required>
            </div>
            <div class="mb-3">
              <label class="form-label">Description</label>
              <input name="desc" class="form-control" required>
            </div>
            <button class="btn btn-success w-100">Add</button>
          </form>
        </div>
      </div>
    </div>

    <div class="col-md-4">
      <div class="card shadow-sm">
        <div class="card-body">
          <h6 class="mb-3">Add Project</h6>
          <form method="post">
            <input type="hidden" name="kind" value="project">
            <div class="mb-2">
              <label class="form-label">Project Number (5 digits)</label>
              <input type="number" min="10000" max="99999" name="number" class="form-control" required>
            </div>
            <div class="mb-2">
              <label class="form-label">Client</label>
              <input name="client" class="form-control" required>
            </div>
            <div class="mb-3">
              <label class="form-label">Description</label>
              <input name="desc" class="form-control" required>
            </div>
            <button class="btn btn-success w-100">Add</button>
          </form>
        </div>
      </div>
    </div>
  </div>

  <div class="card shadow-sm mt-4">
    <div class="card-body">
      <h6 class="mb-3">Log Work Hours</h6>
      <form method="post" class="row g-2">
        <input type="hidden" name="kind" value="hour">
        <div class="col-12 col-md">
          <label class="form-label">Date</label>
          <input type="date" name="date" class="form-control" required value="{{ '%Y-%m-%d'|strftime }}">
        </div>
        <div class="col-12 col-md">
          <label class="form-label">Person</label>
          <select name="person_id" class="form-select" required>
            <option value="">Select...</option>
            {% for p in people %}
              <option value="{{ p.id }}">{{ p.name }} ({{ p.classification }})</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-12 col-md">
          <label class="form-label">Team</label>
          <select name="team_id" class="form-select" required>
            <option value="">Select...</option>
            {% for t in teams %}
              <option value="{{ t.id }}">{{ t.code }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-12 col-md">
          <label class="form-label">Project</label>
          <select name="project_id" class="form-select" required>
            <option value="">Select...</option>
            {% for pr in projects %}
              <option value="{{ pr.id }}">{{ pr.number }} — {{ pr.description }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-6 col-md">
          <label class="form-label">Entry</label>
          <input type="time" name="entry" class="form-control" required>
        </div>
        <div class="col-6 col-md">
          <label class="form-label">Exit</label>
          <input type="time" name="exit" class="form-control" required>
        </div>
        <div class="col-12 col-md-auto align-self-end">
          <button class="btn btn-primary w-100">Add Hours</button>
        </div>
      </form>
    </div>
  </div>

  <div class="d-flex justify-content-between align-items-center mt-4">
    <h6 class="mb-0">Logged Hours</h6>
    <a class="btn btn-outline-primary btn-sm" href="{{ url_for('export_csv') }}">Export CSV</a>
  </div>

  <div class="table-responsive mt-2">
    <table class="table table-sm table-striped align-middle">
      <thead class="table-light">
        <tr>
          <th>Date</th><th>Person</th><th>Team</th><th>Project</th>
          <th>Entry</th><th>Exit</th><th class="text-end">Hours</th>
        </tr>
      </thead>
      <tbody>
        {% for h in hours %}
          <tr>
            <td>{{ h.date }}</td>
            <td>{{ people|selectattr('id','equalto',h.person_id)|first.name if people|selectattr('id','equalto',h.person_id)|list else h.person_id }}</td>
            <td>{{ teams|selectattr('id','equalto',h.team_id)|first.code if teams|selectattr('id','equalto',h.team_id)|list else h.team_id }}</td>
            <td>{{ projects|selectattr('id','equalto',h.project_id)|first.number if projects|selectattr('id','equalto',h.project_id)|list else h.project_id }}</td>
            <td>{{ h.entry }}</td>
            <td>{{ h.exit }}</td>
            <td class="text-end">{{ '%.2f'|format(h.worked_hours) }}</td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="text-muted">No hours yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="row g-3 mt-4">
    <div class="col-md-4">
      <div class="card shadow-sm">
        <div class="card-body">
          <h6 class="mb-2">People</h6>
          <ul class="list-group list-group-flush small">
            {% for p in people %}
              <li class="list-group-item d-flex justify-content-between">
                <span>{{ p.name }}</span><span class="text-muted">{{ p.classification }}</span>
              </li>
            {% else %}
              <li class="list-group-item text-muted">No people added.</li>
            {% endfor %}
          </ul>
        </div>
      </div>
    </div>
    <div class="col-md-4">
      <div class="card shadow-sm">
        <div class="card-body">
          <h6 class="mb-2">Teams</h6>
          <ul class="list-group list-group-flush small">
            {% for t in teams %}
              <li class="list-group-item">{{ t.code }} — <span class="text-muted">{{ t.description }}</span></li>
            {% else %}
              <li class="list-group-item text-muted">No teams added.</li>
            {% endfor %}
          </ul>
        </div>
      </div>
    </div>
    <div class="col-md-4">
      <div class="card shadow-sm">
        <div class="card-body">
          <h6 class="mb-2">Projects</h6>
          <ul class="list-group list-group-flush small">
            {% for pr in projects %}
              <li class="list-group-item">#{{ pr.number }} — <span class="text-muted">{{ pr.client }}</span></li>
            {% else %}
              <li class="list-group-item text-muted">No projects added.</li>
            {% endfor %}
          </ul>
        </div>
      </div>
    </div>
  </div>

</div>
</body>
</html>
"""

# -------------------- Run --------------------
if __name__ == '__main__':
    # bind to all interfaces so you can use it from any device on the network
    port = int(os.getenv('PORT', '5000'))
    app.run(debug=True, host='0.0.0.0', port=port)
