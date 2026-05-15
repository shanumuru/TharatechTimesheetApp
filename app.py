import os
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from extensions import db, login_manager, oauth
from dotenv import load_dotenv

load_dotenv()

# Allow OAuth over plain HTTP in local development only
if os.environ.get('FLASK_ENV') == 'development':
    os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-in-production')

    # Use PostgreSQL on Railway (DATABASE_URL), fall back to SQLite locally
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///timesheet.db')
    # Railway provides postgres:// but SQLAlchemy requires postgresql://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url

    if database_url.startswith('sqlite'):
        print('WARNING: Using SQLite — data will be lost on Railway redeploy. Set DATABASE_URL to use PostgreSQL.')
    else:
        print('INFO: Using PostgreSQL — data will persist across redeploys.')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 'static', 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB
    app.config['ALLOWED_EXTENSIONS'] = {'jpg', 'jpeg', 'png', 'gif'}
    app.config['ANTHROPIC_API_KEY'] = os.environ.get('ANTHROPIC_API_KEY', '')
    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    app.config['ADMIN_EMAIL'] = 'priya.ecm@gmail.com'

    db.init_app(app)
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    from routes.auth import auth_bp
    from routes.admin import admin_bp
    from routes.user import user_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(user_bp, url_prefix='/user')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    with app.app_context():
        db.create_all()
        from sqlalchemy import text, inspect as sa_inspect
        try:
            p_cols = [c['name'] for c in sa_inspect(db.engine).get_columns('projects')]
            if 'applies_to' not in p_cols:
                db.session.execute(text('ALTER TABLE projects ADD COLUMN applies_to VARCHAR(50)'))
                db.session.commit()
        except Exception:
            pass
        try:
            att_cols = [c['name'] for c in sa_inspect(db.engine).get_columns('attachments')]
            if 'extracted_hours' not in att_cols:
                db.session.execute(text('ALTER TABLE attachments ADD COLUMN extracted_hours REAL'))
                db.session.commit()
        except Exception:
            pass
        try:
            ts_cols = [c['name'] for c in sa_inspect(db.engine).get_columns('timesheet_statuses')]
            if 'rejection_reason' not in ts_cols:
                db.session.execute(text('ALTER TABLE timesheet_statuses ADD COLUMN rejection_reason TEXT'))
                db.session.commit()
        except Exception:
            pass
        try:
            u_cols = [c['name'] for c in sa_inspect(db.engine).get_columns('users')]
            if 'google_id' not in u_cols:
                db.session.execute(text('ALTER TABLE users ADD COLUMN google_id VARCHAR(256)'))
                db.session.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id ON users (google_id)'))
                db.session.commit()
        except Exception:
            pass
        try:
            u_cols = [c['name'] for c in sa_inspect(db.engine).get_columns('users')]
            if 'user_type' not in u_cols:
                db.session.execute(text('ALTER TABLE users ADD COLUMN user_type VARCHAR(20)'))
                db.session.commit()
        except Exception:
            pass

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV') == 'development')
