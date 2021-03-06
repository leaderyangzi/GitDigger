from config import points
from datetime import datetime
from app import app, db, login_manager
from app.models.user import User
from app.models.point_log import PointLog
from app.models.repository import Repository
from app.helpers.github_helper import GitHubHelper
from app.helpers.application_helper import flash
from flask import Blueprint, url_for, render_template
from flask import jsonify, request, redirect, session, abort
from wtforms import TextField, TextAreaField, PasswordField, validators
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import logout_user, login_required, login_user, current_user
from flask_wtf import FlaskForm as Form

users = Blueprint('users', __name__)
github_helper = GitHubHelper(app)

class RegistrationForm(Form):
    username = TextField('Username', [validators.Length(min=4, max=32)])
    email = TextField('Email Address', [validators.Length(min=6, max=32)])
    password = PasswordField('Password', [validators.Required()])

    def validate(self):
        if not Form.validate(self):
            return False
        if User.query.filter_by(username=self.username.data).first():
            self.username.errors.append('Username is already taken')
            return False
        if User.query.filter_by(username=self.email.data).first():
            self.email.errors.append('Email is already taken')
            return False
        return True

class LoginForm(Form):
    user = None
    login = TextField('Username or email address', [
        validators.Required(),
        validators.Length(min=4, max=32)
    ])
    password = PasswordField('Password', [
        validators.Required()
    ])

    def validate(self):
        print 'validate'
        if not Form.validate(self):
            print 'validate False'
            return False
        login = self.login.data
        if login[1:-1].find('@') >= 0:
            user = User.query.filter_by(email=login).first()
            login_type = 'email'
        else:
            user = User.query.filter_by(username=login).first()
            login_type = 'username'
        if user is None:
            self.login.errors.append('Unknown %s' % login_type)
            return False
        if not check_password_hash(user.password, self.password.data):
            self.password.errors.append('Invalid password')
            return False
        self.user = user
        return True

class ProfileForm(Form):
    name = TextField('Name', [validators.Length(max=32)])
    bio = TextAreaField('Bio', [validators.Length(max=160)])

def get_login_reward(user):
    now = datetime.now()
    if now.date() == user.last_login_reward_at.date():
        return False
    log = PointLog('login_reward', points.LOGIN_REWARD, None, user)
    user.points += points.LOGIN_REWARD
    user.last_login_reward_at = now
    db.session.add(log)
    return True

@users.route('/join', methods=['GET', 'POST'])
def join():
    form = RegistrationForm(request.form)
    if request.method == 'POST' and form.validate():
        pw_hash = generate_password_hash(form.password.data)
        user = User(form.username.data, form.email.data, pw_hash)
        db.session.add(user)
        db.session.commit()
        user.points = points.SIGNUP_REWARD
        log = PointLog('SIGNUP_REWARD', points.SIGNUP_REWARD, None, user)
        db.session.add(log)
        db.session.commit()
        flash('Thanks for registering')
        return redirect(url_for('users.login'))
    return render_template('join.html', form=form)

@login_manager.user_loader
def load_user(userid):
    user = User.query.get(int(userid))
    if not user:
        return None
    user.last_active_at = datetime.now()
    if user and get_login_reward(user):
        try:
            db.session.commit()
        except:
            db.session.rollback()
    return user

@users.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@users.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm(request.form)
    if request.method == 'POST' and form.validate_on_submit():
        login_user(form.user)
        flash('Logged in successfully')
        next = request.args.get('next')
        return redirect(next or url_for('index'))
    return render_template('login.html', form=form)

@users.route('/<string:username>')
def show(username):
    return abort(404)

@users.route('/settings')
@login_required
def settings():
    return redirect(url_for('users.profile'))

@users.route('/settings/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = current_user
    terms = db.or_(PointLog.sender_id==user.id, PointLog.receiver_id==user.id)
    query = PointLog.query.filter(terms).order_by(PointLog.created_at.desc())
    form = ProfileForm(request.form, name=user.name, bio=user.bio)
    if request.method == 'POST' and form.validate_on_submit():
        current_user.name = form.name.data
        current_user.bio = form.bio.data
        db.session.commit()
        flash('Profile updated successfully')
    return render_template('settings/profile.html', form=form,
                            sidebar_active='profile',
                            point_logs=query.limit(20))

@users.route('/settings/account', methods=['GET', 'POST'])
@login_required
def account():
    return render_template('settings/account.html', sidebar_active='account')

@users.route('/settings/repositories', methods=['GET', 'POST'])
@login_required
def repositories():
    return render_template('settings/repositories.html',
                            sidebar_active='repositories')

@users.route('/settings/github', methods=['GET', 'POST'])
@login_required
def user_github():
    if request.method == 'POST' and current_user.is_authenticated:
        action = request.form.get('action')
        if action == 'unlink':
            current_user.github_id = None
            current_user.github_token = None
            current_user.github_username = None
            db.session.commit()
    repos = Repository.query.filter_by(
        owner_id=current_user.id,
        imported_from='GitHub'
    ).order_by(Repository.name).all()
    integration = github_helper.get_integration()
    return render_template('settings/github.html', integration=integration,
                            repos=repos, sidebar_active='github')

@users.route('/settings/github/install', methods=['GET', 'POST'])
@login_required
def user_github_install():
    integration = github_helper.get_integration()
    if integration:
        return redirect(url_for('users.user_github'))
    app_url = 'https://github.com/apps/'
    app_url += app.config['GITHUB_APP_NAME']
    return redirect(app_url)

@users.route('/auth/github')
def auth_github():
    session['next_url'] = request.args.get('next')
    return github_helper.authorize()

@users.route('/auth/github/callback')
@github_helper.authorized_handler
def authorized(access_token):
    next_url = session.get('next_url')
    if next_url is None:
        next_url = url_for('index')
    else:
        session.pop('next_url')
    if access_token is None:
        return redirect(next_url)
    session['github_token'] = access_token
    user = github_helper.get_user()
    if user is None:
        return redirect(next_url)
    if current_user.is_authenticated:
        current_user.github_id = user['id']
        current_user.github_token = access_token
        current_user.github_username = user['login']
        if not current_user.avatar_url:
            current_user.avatar_url = user['avatar_url']
        db.session.commit()
        return redirect(next_url)
    user = User.query.filter_by(github_id=user['id']).first()
    if user is not None:
        login_user(user)
    return redirect(next_url)

@github_helper.access_token_getter
def token_getter():
    token = session.get('github_token')
    if token is not None:
        return token
    if current_user.github_token:
        return current_user.github_token

@users.route('/api/users/<string:username>', methods=['GET'])
def get(username):
    user = User.query.filter_by(username=username).first()
    if user:
        return jsonify(status=0, user=user)
    return jsonify(status=404)

app.register_blueprint(users)
