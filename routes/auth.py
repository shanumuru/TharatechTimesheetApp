from flask import Blueprint, redirect, url_for, flash, current_app, render_template
from flask_login import login_user, logout_user, login_required, current_user
from extensions import db, login_manager, oauth
from models import User

auth_bp = Blueprint('auth', __name__)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@auth_bp.route('/')
@auth_bp.route('/login')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard') if current_user.is_admin()
                        else url_for('user.dashboard'))
    return render_template('auth/login.html')


@auth_bp.route('/login/google')
def google_login():
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/auth/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        flash('Google login failed. Please try again.', 'danger')
        return redirect(url_for('auth.login'))

    userinfo = token.get('userinfo') or oauth.google.userinfo()
    email = userinfo.get('email', '').lower()
    google_id = userinfo.get('sub')
    name = userinfo.get('name') or email.split('@')[0]

    if not email:
        flash('Could not retrieve email from Google. Please try again.', 'danger')
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            username=name,
            email=email,
            google_id=google_id,
            role='admin' if email == current_app.config['ADMIN_EMAIL'] else 'user',
        )
        user.password_hash = ''  # not used — Google OAuth only
        db.session.add(user)
        db.session.commit()
    else:
        if not user.google_id:
            user.google_id = google_id
            db.session.commit()

    if not user.active:
        flash('Your account has been deactivated. Contact the administrator.', 'danger')
        return redirect(url_for('auth.login'))

    login_user(user)
    return redirect(url_for('admin.dashboard') if user.is_admin() else url_for('user.dashboard'))


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))
