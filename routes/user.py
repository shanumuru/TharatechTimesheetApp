import calendar
import os
import uuid
from datetime import date, datetime
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app, send_from_directory, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from extensions import db
from models import PayPeriod, Project, Attachment, TimesheetStatus, PayPeriodSummary

user_bp = Blueprint('user', __name__)

LOCKED_STATUSES = {'submitted', 'approved'}  # rejected is intentionally excluded — user can re-edit


def _gen_upcoming_pay_periods():
    """Return pay periods for the current month and the following 2 months."""
    today = date.today()
    year, month = today.year, today.month
    periods = []
    for _ in range(3):
        last_day = calendar.monthrange(year, month)[1]
        month_name = date(year, month, 1).strftime('%B')
        for period_num in (1, 2):
            start = date(year, month, 1) if period_num == 1 else date(year, month, 16)
            end = date(year, month, 15) if period_num == 1 else date(year, month, last_day)
            label = (f"{month_name} {year} – Period {period_num} "
                     f"({start.strftime('%b %d')} – {end.strftime('%b %d')})")
            periods.append({'year': year, 'month': month, 'period_num': period_num, 'label': label})
        month += 1
        if month > 12:
            month = 1
            year += 1
    return periods  # rejected is intentionally excluded — user can re-edit


def _allowed_file(filename):
    allowed = current_app.config['ALLOWED_EXTENSIONS']
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


def _get_ts(user_id, pay_period_id):
    return TimesheetStatus.query.filter_by(
        user_id=user_id, pay_period_id=pay_period_id).first()


def _is_locked(user_id, pay_period_id):
    ts = _get_ts(user_id, pay_period_id)
    return ts is not None and ts.status in LOCKED_STATUSES


def _total_hours(user_id, pay_period_id):
    summaries = PayPeriodSummary.query.filter_by(
        user_id=user_id, pay_period_id=pay_period_id).all()
    return sum(s.total_hours for s in summaries)


@user_bp.route('/')
@login_required
def dashboard():
    # --- Editable timesheets: have hours entered, not yet submitted/approved ---
    summary_pp_ids = [row[0] for row in
                      db.session.query(PayPeriodSummary.pay_period_id)
                      .filter_by(user_id=current_user.id).distinct().all()]

    editable_data = []
    for pp_id in summary_pp_ids:
        ts = _get_ts(current_user.id, pp_id)
        if ts is None or ts.status not in ('submitted', 'approved'):
            pp = PayPeriod.query.get(pp_id)
            editable_data.append({
                'pay_period': pp,
                'total_hours': _total_hours(current_user.id, pp_id),
                'attachment_count': Attachment.query.filter_by(
                    user_id=current_user.id, pay_period_id=pp_id).count(),
                'status': ts.status if ts else 'draft',
                'rejection_reason': ts.rejection_reason if ts else None,
            })
    editable_data.sort(
        key=lambda x: (x['pay_period'].year, x['pay_period'].month, x['pay_period'].period_num),
        reverse=True)

    # --- Submitted / approved timesheets ---
    submitted_and_approved = (TimesheetStatus.query
                               .filter(TimesheetStatus.user_id == current_user.id,
                                       TimesheetStatus.status.in_(['submitted', 'approved']))
                               .join(PayPeriod)
                               .order_by(PayPeriod.year.desc(), PayPeriod.month.desc(),
                                         PayPeriod.period_num.desc())
                               .all())
    past_data = []
    for ts in submitted_and_approved:
        pp = ts.pay_period
        past_data.append({
            'pay_period': pp,
            'total_hours': _total_hours(current_user.id, pp.id),
            'attachment_count': Attachment.query.filter_by(
                user_id=current_user.id, pay_period_id=pp.id).count(),
            'status': ts.status,
            'approved_at': ts.approved_at,
        })

    available_periods = _gen_upcoming_pay_periods()

    return render_template('user/dashboard.html',
                           editable_data=editable_data,
                           past_data=past_data,
                           available_periods=available_periods)


@user_bp.route('/timesheet/<int:year>/<int:month>/<int:period_num>', methods=['GET', 'POST'])
@login_required
def pay_period(year, month, period_num):
    if period_num not in (1, 2):
        abort(404)

    pp = PayPeriod.get_or_create(year, month, period_num)
    locked = _is_locked(current_user.id, pp.id)
    ts = _get_ts(current_user.id, pp.id)
    projects = Project.query.filter_by(active=True).order_by(Project.name).all()

    if request.method == 'POST':
        action = request.form.get('action')

        if locked:
            flash('This timesheet is locked and cannot be edited.', 'warning')
            return redirect(url_for('user.pay_period', year=year, month=month,
                                    period_num=period_num))

        if action in ('save', 'submit'):
            for project in projects:
                key = f'hours_{project.id}'
                hours_str = request.form.get(key, '').strip()
                try:
                    hours = max(0.0, float(hours_str)) if hours_str else 0.0
                except ValueError:
                    hours = 0.0

                existing = PayPeriodSummary.query.filter_by(
                    user_id=current_user.id,
                    pay_period_id=pp.id,
                    project_id=project.id
                ).first()

                if hours > 0:
                    if existing:
                        existing.total_hours = hours
                    else:
                        db.session.add(PayPeriodSummary(
                            user_id=current_user.id,
                            pay_period_id=pp.id,
                            project_id=project.id,
                            total_hours=hours
                        ))
                elif existing:
                    db.session.delete(existing)

            if action == 'submit':
                # Always save hours first so nothing is lost
                db.session.commit()

                total_hours = sum(
                    max(0.0, float(request.form.get(f'hours_{p.id}', '') or 0))
                    for p in projects
                )
                att_count = Attachment.query.filter_by(
                    user_id=current_user.id, pay_period_id=pp.id).count()

                if total_hours <= 0:
                    flash('Please enter your total hours worked before submitting.', 'danger')
                    return redirect(url_for('user.pay_period', year=year, month=month,
                                            period_num=period_num))

                if att_count == 0:
                    flash('Please upload your Mindcomputing timesheet screenshot before submitting.', 'danger')
                    return redirect(url_for('user.pay_period', year=year, month=month,
                                            period_num=period_num))

                ts = _get_ts(current_user.id, pp.id)
                if not ts:
                    ts = TimesheetStatus(user_id=current_user.id, pay_period_id=pp.id)
                    db.session.add(ts)
                ts.status = 'submitted'
                db.session.commit()
                flash('Timesheet submitted successfully.', 'success')
                return redirect(url_for('user.dashboard'))

            db.session.commit()
            flash('Timesheet saved successfully.', 'success')
            return redirect(url_for('user.pay_period', year=year, month=month,
                                    period_num=period_num))

    summaries = PayPeriodSummary.query.filter_by(
        user_id=current_user.id, pay_period_id=pp.id).all()
    hours_map = {s.project_id: s.total_hours for s in summaries}

    attachments = (Attachment.query
                   .filter_by(user_id=current_user.id, pay_period_id=pp.id)
                   .order_by(Attachment.uploaded_at).all())

    has_hours = sum(hours_map.values()) > 0
    has_attachments = len(attachments) > 0

    return render_template('user/pay_period.html',
                           pp=pp,
                           projects=projects,
                           hours_map=hours_map,
                           attachments=attachments,
                           ts=ts,
                           locked=locked,
                           has_hours=has_hours,
                           has_attachments=has_attachments)


@user_bp.route('/timesheet/<int:year>/<int:month>/<int:period_num>/upload',
               methods=['POST'])
@login_required
def upload_attachment(year, month, period_num):
    pp = PayPeriod.get_or_create(year, month, period_num)

    if _is_locked(current_user.id, pp.id):
        flash('This timesheet is locked and cannot be modified.', 'warning')
        return redirect(url_for('user.pay_period', year=year, month=month,
                                period_num=period_num))

    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'danger')
    elif not _allowed_file(file.filename):
        flash('Only JPG, PNG, and GIF files are allowed.', 'danger')
    else:
        original_filename = secure_filename(file.filename)
        ext = original_filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename))
        db.session.add(Attachment(
            user_id=current_user.id,
            pay_period_id=pp.id,
            filename=unique_filename,
            original_filename=original_filename,
            file_type=ext
        ))
        db.session.commit()
        flash(f'"{original_filename}" uploaded successfully.', 'success')

    return redirect(url_for('user.pay_period', year=year, month=month,
                            period_num=period_num))


@user_bp.route('/attachment/<int:attachment_id>/delete', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    if attachment.user_id != current_user.id and not current_user.is_admin():
        abort(403)

    if _is_locked(current_user.id, attachment.pay_period_id):
        flash('This timesheet is locked and cannot be modified.', 'warning')
        pp = attachment.pay_period
        return redirect(url_for('user.pay_period', year=pp.year, month=pp.month,
                                period_num=pp.period_num))

    try:
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError:
        pass

    pp = attachment.pay_period
    db.session.delete(attachment)
    db.session.commit()
    flash('Attachment deleted.', 'success')
    return redirect(url_for('user.pay_period', year=pp.year, month=pp.month,
                            period_num=pp.period_num))


@user_bp.route('/attachment/<int:attachment_id>/view')
@login_required
def view_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    if attachment.user_id != current_user.id and not current_user.is_admin():
        abort(403)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'],
                               attachment.filename,
                               download_name=attachment.original_filename)
