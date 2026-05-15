import base64
import calendar
import os
from datetime import date, datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from extensions import db
from models import User, Project, PayPeriod, TimeEntry, Attachment, TimesheetStatus, PayPeriodSummary


admin_bp = Blueprint('admin', __name__)

_MEDIA_TYPES = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif'}


def _extract_hours_from_image(filepath, ext):
    """Call Claude vision to read total hours from a timesheet screenshot."""
    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None
    try:
        import anthropic
        import httpx
        with open(filepath, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')
        media_type = _MEDIA_TYPES.get(ext.lower(), 'image/jpeg')
        # Use verify=False to work around corporate SSL inspection certificates
        http_client = httpx.Client(verify=False)
        client = anthropic.Anthropic(api_key=api_key, http_client=http_client)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=64,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image',
                     'source': {'type': 'base64', 'media_type': media_type, 'data': image_data}},
                    {'type': 'text',
                     'text': ('This is a work timesheet screenshot. '
                              'Find the grand total hours worked and reply with ONLY that number '
                              '(e.g. "72" or "40.5"). '
                              'If you cannot find a clear total, reply with "UNKNOWN".')}
                ]
            }]
        )
        text = msg.content[0].text.strip().replace(',', '')
        return float(text)
    except Exception:
        return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('Admin access required.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def _gen_pay_periods(n=24):
    today = date.today()
    year, month = today.year, today.month
    period_num = 1 if today.day <= 15 else 2
    periods = []
    for _ in range(n):
        if period_num == 1:
            start = date(year, month, 1)
            end = date(year, month, 15)
        else:
            start = date(year, month, 16)
            last_day = calendar.monthrange(year, month)[1]
            end = date(year, month, last_day)
        label = (f"{start.strftime('%B %Y')} – Period {period_num} "
                 f"({start.strftime('%b %d')} – {end.strftime('%b %d')})")
        periods.append({'year': year, 'month': month, 'period_num': period_num, 'label': label})
        if period_num == 2:
            period_num = 1
        else:
            period_num = 2
            month -= 1
            if month == 0:
                month = 12
                year -= 1
    return periods


@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    total_users = User.query.filter_by(role='user').count()
    total_projects = Project.query.filter_by(active=True).count()
    total_entries = TimeEntry.query.count()
    recent_users = (User.query.filter_by(role='user')
                    .order_by(User.created_at.desc()).limit(5).all())
    pending_timesheets = (TimesheetStatus.query
                          .filter_by(status='submitted')
                          .order_by(TimesheetStatus.id.desc())
                          .all())
    return render_template('admin/dashboard.html',
                           total_users=total_users,
                           total_projects=total_projects,
                           total_entries=total_entries,
                           recent_users=recent_users,
                           pending_timesheets=pending_timesheets)


@admin_bp.route('/projects', methods=['GET', 'POST'])
@login_required
@admin_required
def projects():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            if not name:
                flash('Project name is required.', 'danger')
            elif Project.query.filter_by(name=name).first():
                flash('A project with that name already exists.', 'danger')
            else:
                project = Project(name=name, description=description,
                                  created_by_id=current_user.id)
                db.session.add(project)
                db.session.commit()
                flash(f'Project "{name}" created.', 'success')

        elif action == 'edit':
            project = Project.query.get_or_404(request.form.get('project_id'))
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            if not name:
                flash('Project name is required.', 'danger')
            else:
                existing = Project.query.filter_by(name=name).first()
                if existing and existing.id != project.id:
                    flash('A project with that name already exists.', 'danger')
                else:
                    project.name = name
                    project.description = description
                    db.session.commit()
                    flash(f'Project "{name}" updated.', 'success')

        elif action == 'toggle':
            project = Project.query.get_or_404(request.form.get('project_id'))
            project.active = not project.active
            db.session.commit()
            status = 'activated' if project.active else 'deactivated'
            flash(f'Project "{project.name}" {status}.', 'success')

        return redirect(url_for('admin.projects'))

    projects_list = Project.query.order_by(Project.active.desc(), Project.name).all()
    return render_template('admin/projects.html', projects=projects_list)


@admin_bp.route('/users', methods=['GET', 'POST'])
@login_required
@admin_required
def users():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            role = request.form.get('role', 'user')
            if not all([username, email, password]):
                flash('All fields are required.', 'danger')
            elif User.query.filter_by(username=username).first():
                flash('Username already exists.', 'danger')
            elif User.query.filter_by(email=email).first():
                flash('Email already exists.', 'danger')
            else:
                user = User(username=username, email=email, role=role)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash(f'User "{username}" created.', 'success')

        elif action == 'toggle':
            user = User.query.get_or_404(request.form.get('user_id'))
            if user.id == current_user.id:
                flash('Cannot deactivate yourself.', 'danger')
            else:
                user.active = not user.active
                db.session.commit()
                status = 'activated' if user.active else 'deactivated'
                flash(f'User "{user.username}" {status}.', 'success')

        elif action == 'reset_password':
            user = User.query.get_or_404(request.form.get('user_id'))
            new_password = request.form.get('new_password', '')
            if not new_password:
                flash('New password is required.', 'danger')
            else:
                user.set_password(new_password)
                db.session.commit()
                flash(f'Password reset for "{user.username}".', 'success')

        elif action == 'set_user_type':
            user = User.query.get_or_404(request.form.get('user_id'))
            user_type = request.form.get('user_type', '').strip()
            if user_type in ('contractor', 'employee', ''):
                user.user_type = user_type or None
                db.session.commit()
                flash(f'User type updated for "{user.username}".', 'success')
            else:
                flash('Invalid user type.', 'danger')

        return redirect(url_for('admin.users'))

    users_list = User.query.order_by(User.role, User.username).all()
    return render_template('admin/users.html', users=users_list)


@admin_bp.route('/timesheets')
@login_required
@admin_required
def timesheets():
    users_list = User.query.filter_by(role='user', active=True).order_by(User.username).all()

    sel_user_id = request.args.get('user_id', type=int)

    # Only show pay periods where the selected user has submitted or approved timesheets.
    if sel_user_id:
        available_periods = (PayPeriod.query
                             .join(TimesheetStatus,
                                   (TimesheetStatus.pay_period_id == PayPeriod.id) &
                                   (TimesheetStatus.user_id == sel_user_id))
                             .filter(TimesheetStatus.status.in_(['submitted', 'approved', 'rejected']))
                             .order_by(PayPeriod.year.desc(), PayPeriod.month.desc(),
                                       PayPeriod.period_num.desc())
                             .all())
    else:
        available_periods = []

    sel_pp_id = request.args.get('pay_period_id', type=int)

    selected_user = None
    selected_pay_period = None
    summaries = []
    attachments_list = []
    ts = None

    screenshot_total = None   # sum of hours extracted from all attachments
    screenshot_analyzed = False  # True once we have at least one analyzable attachment

    if sel_user_id and sel_pp_id:
        selected_user = User.query.get(sel_user_id)
        selected_pay_period = PayPeriod.query.get(sel_pp_id)
        if selected_user and selected_pay_period:
            summaries = (PayPeriodSummary.query
                         .filter_by(user_id=sel_user_id, pay_period_id=sel_pp_id)
                         .join(Project).order_by(Project.name).all())
            attachments_list = (Attachment.query
                                .filter_by(user_id=sel_user_id, pay_period_id=sel_pp_id)
                                .order_by(Attachment.uploaded_at).all())
            ts = TimesheetStatus.query.filter_by(user_id=sel_user_id,
                                                 pay_period_id=sel_pp_id).first()

            # Analyze each attachment if not done yet
            upload_folder = current_app.config['UPLOAD_FOLDER']
            for att in attachments_list:
                if att.extracted_hours is None:
                    fp = os.path.join(upload_folder, att.filename)
                    if os.path.exists(fp):
                        att.extracted_hours = _extract_hours_from_image(fp, att.file_type or 'jpg')
                        db.session.commit()

            # Build screenshot total (sum across all attachments that returned a value)
            valid = [att.extracted_hours for att in attachments_list if att.extracted_hours is not None]
            if valid:
                screenshot_total = sum(valid)
                screenshot_analyzed = True
            elif attachments_list and current_app.config.get('ANTHROPIC_API_KEY'):
                screenshot_analyzed = True  # analyzed but nothing extractable

    timesheet_total = sum(s.total_hours for s in summaries)

    return render_template('admin/timesheets.html',
                           users=users_list,
                           available_periods=available_periods,
                           sel_user_id=sel_user_id,
                           selected_user=selected_user,
                           selected_pay_period=selected_pay_period,
                           summaries=summaries,
                           attachments=attachments_list,
                           timesheet_status=ts,
                           timesheet_total=timesheet_total,
                           screenshot_total=screenshot_total,
                           screenshot_analyzed=screenshot_analyzed)


@admin_bp.route('/timesheets/approve', methods=['POST'])
@login_required
@admin_required
def approve_timesheet():
    user_id = request.form.get('user_id', type=int)
    pay_period_id = request.form.get('pay_period_id', type=int)

    ts = TimesheetStatus.query.filter_by(user_id=user_id, pay_period_id=pay_period_id).first()
    if not ts:
        ts = TimesheetStatus(user_id=user_id, pay_period_id=pay_period_id)
        db.session.add(ts)

    ts.status = 'approved'
    ts.approved_at = datetime.utcnow()
    ts.approved_by_id = current_user.id
    ts.rejection_reason = None
    db.session.commit()
    flash('Timesheet approved successfully.', 'success')
    return redirect(url_for('admin.timesheets', user_id=user_id, pay_period_id=pay_period_id))


@admin_bp.route('/timesheets/reject', methods=['POST'])
@login_required
@admin_required
def reject_timesheet():
    user_id = request.form.get('user_id', type=int)
    pay_period_id = request.form.get('pay_period_id', type=int)
    reason = request.form.get('rejection_reason', '').strip()

    ts = TimesheetStatus.query.filter_by(user_id=user_id, pay_period_id=pay_period_id).first()
    if not ts:
        ts = TimesheetStatus(user_id=user_id, pay_period_id=pay_period_id)
        db.session.add(ts)

    ts.status = 'rejected'
    ts.rejection_reason = reason or None
    ts.approved_at = None
    ts.approved_by_id = None
    db.session.commit()
    flash('Timesheet rejected. The user can now edit and resubmit it.', 'warning')
    return redirect(url_for('admin.timesheets', user_id=user_id, pay_period_id=pay_period_id))
