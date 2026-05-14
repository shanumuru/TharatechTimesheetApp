from datetime import datetime, date, timedelta
import calendar
from extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    google_id = db.Column(db.String(256), unique=True, nullable=True)
    role = db.Column(db.String(20), nullable=False, default='user')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    time_entries = db.relationship('TimeEntry', backref='user', lazy='dynamic',
                                   foreign_keys='TimeEntry.user_id')
    attachments = db.relationship('Attachment', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'


class Project(db.Model):
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    description = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('User', backref='created_projects', foreign_keys=[created_by_id])
    time_entries = db.relationship('TimeEntry', backref='project', lazy='dynamic')

    def __repr__(self):
        return f'<Project {self.name}>'


class PayPeriod(db.Model):
    __tablename__ = 'pay_periods'

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    period_num = db.Column(db.Integer, nullable=False)  # 1 = days 1-15, 2 = days 16-end
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)

    __table_args__ = (db.UniqueConstraint('year', 'month', 'period_num'),)

    time_entries = db.relationship('TimeEntry', backref='pay_period', lazy='dynamic')
    attachments = db.relationship('Attachment', backref='pay_period', lazy='dynamic')

    @property
    def label(self):
        month_name = date(self.year, self.month, 1).strftime('%B')
        return (f"{month_name} {self.year} – Period {self.period_num} "
                f"({self.start_date.strftime('%b %d')} – {self.end_date.strftime('%b %d')})")

    @property
    def dates(self):
        result = []
        current = self.start_date
        while current <= self.end_date:
            result.append(current)
            current += timedelta(days=1)
        return result

    @classmethod
    def get_or_create(cls, year, month, period_num):
        pp = cls.query.filter_by(year=year, month=month, period_num=period_num).first()
        if not pp:
            if period_num == 1:
                start = date(year, month, 1)
                end = date(year, month, 15)
            else:
                start = date(year, month, 16)
                last_day = calendar.monthrange(year, month)[1]
                end = date(year, month, last_day)
            pp = cls(year=year, month=month, period_num=period_num,
                     start_date=start, end_date=end)
            db.session.add(pp)
            db.session.commit()
        return pp

    def __repr__(self):
        return f'<PayPeriod {self.year}-{self.month}-{self.period_num}>'


class TimeEntry(db.Model):
    __tablename__ = 'time_entries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    pay_period_id = db.Column(db.Integer, db.ForeignKey('pay_periods.id'), nullable=False)
    entry_date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'project_id', 'entry_date'),)


class Attachment(db.Model):
    __tablename__ = 'attachments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pay_period_id = db.Column(db.Integer, db.ForeignKey('pay_periods.id'), nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    original_filename = db.Column(db.String(256), nullable=False)
    file_type = db.Column(db.String(10))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    extracted_hours = db.Column(db.Float, nullable=True)


class PayPeriodSummary(db.Model):
    __tablename__ = 'pay_period_summaries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pay_period_id = db.Column(db.Integer, db.ForeignKey('pay_periods.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    total_hours = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'pay_period_id', 'project_id'),)

    user = db.relationship('User', foreign_keys=[user_id])
    pay_period = db.relationship('PayPeriod', backref='summaries')
    project = db.relationship('Project', foreign_keys=[project_id])


class TimesheetStatus(db.Model):
    __tablename__ = 'timesheet_statuses'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pay_period_id = db.Column(db.Integer, db.ForeignKey('pay_periods.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='draft')  # draft | submitted | approved | rejected
    approved_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    rejection_reason = db.Column(db.Text)

    __table_args__ = (db.UniqueConstraint('user_id', 'pay_period_id'),)

    user = db.relationship('User', foreign_keys=[user_id])
    pay_period = db.relationship('PayPeriod', backref='statuses')
    approved_by = db.relationship('User', foreign_keys=[approved_by_id])
