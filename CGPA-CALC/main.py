from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from oauthlib.oauth2 import WebApplicationClient
import requests
import json
import os
# Add these imports at the top
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # Add this line near the top of the file
app = Flask(__name__)
app.secret_key = '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'  # Change this to a secure secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cgpa.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Google OAuth2 credentials
GOOGLE_CLIENT_ID = "680247592744-66cblsm9jmkk3tik8uihitlv1fj37npg.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

client = WebApplicationClient(GOOGLE_CLIENT_ID)

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.String(100), primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100), unique=True)
    semesters = db.relationship('Semester', backref='user', lazy=True)

class Semester(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    semester_number = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.String(100), db.ForeignKey('user.id'), nullable=False)
    courses = db.relationship('Course', backref='semester', lazy=True, cascade="all, delete-orphan")

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    credits = db.Column(db.Float, nullable=False)
    grade = db.Column(db.String(2), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login')
def login():
    try:
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        authorization_endpoint = google_provider_cfg["authorization_endpoint"]
        
        # Generate a random state parameter
        session['oauth_state'] = os.urandom(16).hex()
        
        # Ensure this matches exactly what you registered in Google Cloud Console
        callback_url = url_for('callback', _external=True, _scheme='http')
        
        request_uri = client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=callback_url,
            scope=["openid", "email", "profile"],
            state=session['oauth_state']
        )
        return redirect(request_uri)
    except Exception as e:
        print(f"Error in login: {str(e)}")
        return f"Error during login: {str(e)}", 400

@app.route('/login/callback')
def callback():
    try:
        # Get authorization code Google sent back
        code = request.args.get("code")
        if not code:
            return "Authorization code not found.", 400

        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        token_endpoint = google_provider_cfg["token_endpoint"]
        
        # Use the same callback URL as in the login route
        callback_url = url_for('callback', _external=True, _scheme='http')
        
        # Prepare the token request
        token_url, headers, body = client.prepare_token_request(
            token_endpoint,
            authorization_response=request.url,
            redirect_url=callback_url,
            code=code
        )

        # Send the token request
        token_response = requests.post(
            token_url,
            headers=headers,
            data=body,
            auth=(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
        )

        # Check if the token request was successful
        if not token_response.ok:
            return "Failed to get token.", 400

        # Parse the tokens
        client.parse_request_body_response(json.dumps(token_response.json()))
        
        # Get user info from Google
        userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
        uri, headers, body = client.add_token(userinfo_endpoint)
        userinfo_response = requests.get(uri, headers=headers, data=body)
        
        if not userinfo_response.ok:
            return "Failed to get user info.", 400

        if userinfo_response.json().get("email_verified"):
            unique_id = userinfo_response.json()["sub"]
            users_email = userinfo_response.json()["email"]
            users_name = userinfo_response.json()["name"]
            
            # Create or get user from database
            user = User.query.filter_by(id=unique_id).first()
            if not user:
                user = User(
                    id=unique_id,
                    name=users_name,
                    email=users_email
                )
                db.session.add(user)
                db.session.commit()
            
            # Begin user session
            login_user(user)
            
            return redirect(url_for('dashboard'))
        else:
            return "User email not verified by Google.", 400
            
    except Exception as e:
        print(f"Error in callback: {str(e)}")
        return f"Error during authentication: {str(e)}", 400

@app.route('/dashboard')
@login_required
def dashboard():
    semesters = Semester.query.filter_by(user_id=current_user.id).order_by(Semester.semester_number).all()
    
    # Collect all courses from all semesters
    all_courses = []
    for semester in semesters:
        all_courses.extend(semester.courses)
    
    return render_template('dashboard.html', 
                         semesters=semesters,
                         all_courses=all_courses,  # Pass all_courses to template
                         calculate_gpa=calculate_gpa)

@app.route('/add_semester', methods=['POST'])
@login_required
def add_semester():
    semester_number = int(request.form.get('semester_number'))
    if Semester.query.filter_by(user_id=current_user.id, semester_number=semester_number).first():
        flash('Semester number already exists!')
        return redirect(url_for('dashboard'))
    
    new_semester = Semester(semester_number=semester_number, user_id=current_user.id)
    db.session.add(new_semester)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/add_course/<int:semester_id>', methods=['POST'])
@login_required
def add_course(semester_id):
    semester = Semester.query.get_or_404(semester_id)
    if semester.user_id != current_user.id:
        return "Unauthorized", 403
    
    course_name = request.form.get('name')
    new_credits = float(request.form.get('credits'))
    
    # Check if course already exists in this semester
    if any(course.name.lower() == course_name.lower() for course in semester.courses):
        flash('This course already exists in this semester!')
        return redirect(url_for('dashboard'))
    
    # Get all user's semesters and courses
    user_semesters = Semester.query.filter_by(user_id=current_user.id).all()
    course_occurrences = []
    
    for sem in user_semesters:
        for course in sem.courses:
            if course.name.lower() == course_name.lower():
                course_occurrences.append((sem.semester_number, course))
    
    # Check if course has already been taken twice
    if len(course_occurrences) >= 2:
        flash('This course has already been taken twice!')
        return redirect(url_for('dashboard'))
    
    # Check semester credits limit
    total_credits = sum(course.credits for course in semester.courses)
    if total_credits + new_credits > 25:
        flash('Maximum credits (25) exceeded for this semester!')
        return redirect(url_for('dashboard'))
    
    new_course = Course(
        name=course_name,
        credits=new_credits,
        grade=request.form.get('grade'),
        semester_id=semester_id
    )
    db.session.add(new_course)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_course/<int:course_id>')
@login_required
def delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    if course.semester.user_id != current_user.id:
        return "Unauthorized", 403
    
    db.session.delete(course)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_semester/<int:semester_id>')
@login_required
def delete_semester(semester_id):
    semester = Semester.query.get_or_404(semester_id)
    if semester.user_id != current_user.id:
        return "Unauthorized", 403
    
    db.session.delete(semester)
    db.session.commit()
    return redirect(url_for('dashboard'))

def calculate_gpa(courses):
    if not courses:
        return 0.0
    
    grade_points = {
        'A': 10.0, 'A-': 9.0,
        'B': 8.0, 'B-': 7.0,
        'C': 6.0, 'C-': 5.0,
        'D': 4.0, 'F': 0.0
    }
    
    # Group courses by name and get the latest grade for repeated courses
    course_dict = {}
    for course in courses:
        semester = Semester.query.get(course.semester_id)
        if course.name not in course_dict or semester.semester_number > course_dict[course.name]['semester']:
            course_dict[course.name] = {
                'credits': course.credits,
                'grade': course.grade,
                'semester': semester.semester_number
            }
    
    total_points = 0
    total_credits = 0
    
    # Calculate GPA using only the latest grade for each course
    for course_info in course_dict.values():
        if course_info['grade'] in grade_points:
            total_points += grade_points[course_info['grade']] * course_info['credits']
            total_credits += course_info['credits']
    
    return round(total_points / total_credits, 2) if total_credits > 0 else 0.0

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/update_grade/<int:course_id>', methods=['POST'])
@login_required
def update_grade(course_id):
    course = Course.query.get_or_404(course_id)
    if course.semester.user_id != current_user.id:
        return "Unauthorized", 403
    
    new_grade = request.form.get('grade')
    course.grade = new_grade
    db.session.commit()
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='127.0.0.1', port=5000, debug=True)
