from flask import Flask, render_template_string, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, current_user, UserMixin
from functools import wraps
from io import StringIO
import csv

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
db = SQLAlchemy(app)

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True)
    password = db.Column(db.String(150))
    role = db.Column(db.String(50))

class Person(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150))
    classification = db.Column(db.String(50))

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(150))
    description = db.Column(db.String(200))

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True)
    client = db.Column(db.String(150))
    description = db.Column(db.String(200))

class Hour(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10))
    person_id = db.Column(db.Integer)
    team_id = db.Column(db.Integer)
    entry = db.Column(db.String(5))
    exit = db.Column(db.String(5))
    worked_hours = db.Column(db.Float)

# Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def supervisor_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'supervisor':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

# Initialize DB & default admin
@app.before_first_request
def setup():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(username='admin', password='admin', role='supervisor'))
        db.session.commit()

# Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form['username']).first()
        if u and u.password == request.form['password']:
            login_user(u)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template_string(login_html)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@supervisor_required
def dashboard():
    if request.method == 'POST':
        kind = request.form['kind']
        if kind == 'person':
            db.session.add(Person(name=request.form['name'], classification=request.form['class']))
        elif kind == 'team':
            db.session.add(Team(code=request.form['code'], description=request.form['desc']))
        elif kind == 'project':
            db.session.add(Project(number=request.form['number'], client=request.form['client'], description=request.form['desc']))
        elif kind == 'hour':
            h = Hour(
                date=request.form['date'],
                person_id=request.form['person_id'],
                team_id=request.form['team_id'],
                entry=request.form['entry'],
                exit=request.form['exit'],
                worked_hours=float(request.form['worked_hours'])
            )
            db.session.add(h)
        db.session.commit()
        return redirect('/')
    return render_template_string(
        dashboard_html,
        people=Person.query.all(),
        teams=Team.query.all(),
        projects=Project.query.all(),
        hours=Hour.query.all()
    )

@app.route('/export')
@supervisor_required
def export():
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Date', 'Person ID', 'Team ID', 'Entry', 'Exit', 'Worked Hours'])
    for h in Hour.query.all():
        cw.writerow([h.date, h.person_id, h.team_id, h.entry, h.exit, h.worked_hours])
    output = si.getvalue()
    return send_file(StringIO(output), mimetype='text/csv', as_attachment=True, download_name='hours.csv')

# Templates embedded as strings
login_html = """
<!DOCTYPE html>
<html>
<head>
  <title>Login</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
  <div class="container mt-5">
    <h2>Supervisor Login</h2>
    <form method="POST">
      <input name="username" placeholder="Username" class="form-control mb-2">
      <input name="password" type="password" placeholder="Password" class="form-control mb-2">
      <button class="btn btn-primary">Login</button>
    </form>
  </div>
</body>
</html>
"""

dashboard_html = """
<!DOCTYPE html>
<html>
<head>
  <title>Dashboard</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
  <div class="container mt-4">
    <h2>Dashboard</h2>
    <a href="/logout" class="btn btn-danger mb-3">Logout</a>
    
    <h4>Add Person</h4>
    <form method="POST">
      <input type="hidden" name="kind" value="person">
      <input name="name" placeholder="Name" class="form-control mb-2">
      <select name="class" class="form-control mb-2">
        <option value="direto">Direto</option>
        <option value="indireto">Indireto</option>
      </select>
      <button class="btn btn-success mb-4">Add</button>
    </form>
    
    <h4>Add Team</h4>
    <form method="POST">
      <input type="hidden" name="kind" value="team">
      <input name="code" placeholder="Code" class="form-control mb-2">
      <input name="desc" placeholder="Description" class="form-control mb-2">
      <button class="btn btn-success mb-4">Add</button>
    </form>
    
    <h4>Add Project</h4>
    <form method="POST">
      <input type="hidden" name="kind" value="project">
      <input name="number" type="number" placeholder="Project Number" class="form-control mb-2">
      <input name="client" placeholder="Client Name" class="form-control mb-2">
      <input name="desc" placeholder="Description" class="form-control mb-2">
      <button class="btn btn-success mb-4">Add</button>
    </form>
    
    <h4>Log Work Hours</h4>
    <form method="POST">
      <input type="hidden" name="kind" value="hour">
      <input name="date" type="date" class="form-control mb-2">
      <
