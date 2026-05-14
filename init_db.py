from app import create_app
from extensions import db
from models import User


def init_db():
    app = create_app()
    with app.app_context():
        db.create_all()

        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@example.com', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("Created default admin  |  username: admin  /  password: admin123")
        else:
            print("Admin user already exists.")

        print("Database ready.")


if __name__ == '__main__':
    init_db()
