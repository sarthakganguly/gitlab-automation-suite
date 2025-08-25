# /app/main/routes.py
# Contains all the application routes (view functions).

import traceback
import io
from datetime import datetime, timedelta

from flask import (render_template, request, redirect, url_for, flash,
                   session, jsonify, send_file, current_app)
import pandas as pd

from app.main import bp
from app.main.forms import ConnectionForm
from app.main.services import GitLabService
from app.main.logic import ReportGenerator, AutomationLogic

@bp.route('/', methods=['GET', 'POST'])
def index():
    form = ConnectionForm()
    if form.validate_on_submit():
        try:
            gl_service = GitLabService(form.gitlab_url.data, form.access_token.data)
            raw_groups = gl_service.get_user_groups()
            session['cached_groups'] = [{'id': g.id, 'name': g.name} for g in raw_groups] if raw_groups else []
            session['gitlab_url'] = form.gitlab_url.data
            session['access_token'] = form.access_token.data
            session['is_connected'] = True
            flash('Connection to GitLab established successfully!', 'success')
            return redirect(url_for('main.dashboard'))
        except Exception as e:
            flash('Connection failed. Please check your credentials and URL.', 'danger')
            flash(f"Technical Details: {traceback.format_exc()}", 'secondary')
    return render_template('index.html', form=form)

@bp.route('/dashboard')
def dashboard():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    groups = session.get('cached_groups', [])
    return render_template('dashboard.html', groups=groups)

@bp.route('/automations')
def automations_page():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    groups = session.get('cached_groups', [])
    return render_template('automations.html', groups=groups)

@bp.route('/search')
def search_export_page():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    groups = session.get('cached_groups', [])
    return render_template('search_export.html', groups=groups)

@bp.route('/team_activity')
def team_activity_page():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    return render_template('team_activity.html')

@bp.route('/lead_cycle_time')
def lead_cycle_time_page():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    groups = session.get('cached_groups', [])
    return render_template('lead_cycle_time.html', groups=groups)

@bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))

# --- API/AJAX Routes ---

@bp.route('/api/get_group_children', methods=['POST'])
def get_group_children():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    group_id = request.json.get('group_id')
    if not group_id: return jsonify({'error': 'Group ID is required'}), 400
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        data = gl_service.get_group_details(group_id)
        if data is None: return jsonify({'error': 'Group not found or access denied'}), 404
        response_data = {
            'subgroups': [{'id': sg.id, 'name': sg.name} for sg in data['subgroups']],
            'projects': [{'id': p.id, 'name': p.name} for p in data['projects']]
        }
        return jsonify(response_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/generate_report', methods=['POST'])
def generate_report():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    report_type = data.get('report_type')
    
    required_params = ['report_type', 'scope_id']
    if report_type == 'epic_report':
        required_params.append('epic_iid')
    elif report_type == 'defect_trend':
        required_params.extend(['scope_type', 'months', 'qa_labels', 'prod_labels'])
    else:
        required_params.extend(['scope_type', 'start_date', 'end_date'])

    if not all(data.get(k) for k in required_params):
        return jsonify({'error': f'Missing required parameters for {report_type}'}), 400

    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        report_html, error_msg, report_title = "", None, "Report"
        
        if report_type == 'epic_report':
            report_title = "Epic Report"
            report_df, error_msg = ReportGenerator.generate_epic_report(gl_service, data['scope_id'], data['epic_iid'])
            if not error_msg:
                report_html = render_template('_epic_report_results.html', issues=report_df.to_dict('records'))
                return jsonify({'report_html': report_html, 'download_url': url_for('main.download_report', **data), 'title': report_title})
        elif report_type == 'defect_trend':
            report_title = "Defect Escape Trend"
            chart_data, error_msg = ReportGenerator.generate_defect_trend_report(
                gl_service, data['scope_id'], data['scope_type'], data['months'], data['qa_labels'], data['prod_labels']
            )
            if not error_msg:
                return jsonify({'chart_data': chart_data, 'title': report_title})
        elif report_type == 'milestone_analytics':
            report_title = "Milestone Analytics"
            milestones, error_msg = ReportGenerator.generate_milestone_list(gl_service, data['scope_id'], data['start_date'], data['end_date'])
            if not error_msg: report_html = render_template('_milestone_list.html', milestones=milestones, group_id=data['scope_id'])
        else:
            report_df = pd.DataFrame()
            if report_type == 'defect_escape':
                report_title = "Defect Escape Ratio"
                qa_labels = data.get('qa_labels')
                prod_labels = data.get('prod_labels')
                if not qa_labels or not prod_labels: return jsonify({'error': 'QA and Production labels are required'}), 400
                report_df, error_msg = ReportGenerator.generate_defect_escape_report(gl_service, data['scope_id'], data['scope_type'], data['start_date'], data['end_date'], qa_labels, prod_labels)
            elif report_type == 'issue_analytics':
                report_title = "Issue Analytics"
                report_df, error_msg = ReportGenerator.generate_issue_analytics_report(gl_service, data['scope_id'], data['scope_type'], created_after=data['start_date'], created_before=data['end_date'])
            
            if not error_msg: report_html = report_df.to_html(classes='table table-striped table-bordered table-hover table-responsive', index=False, border=0)
        
        if error_msg: return jsonify({'error': error_msg}), 500
        return jsonify({'report_html': report_html, 'download_url': url_for('main.download_report', **data), 'title': report_title})
    except Exception as e:
        current_app.logger.error(f"Report generation failed: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'An internal error occurred: {e}'}), 500

@bp.route('/download_report')
def download_report():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    args = request.args.to_dict()
    report_type = args.get('report_type')
    if not report_type:
        flash('Invalid download request.', 'danger')
        return redirect(url_for('main.dashboard'))
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        report_df, error_msg = pd.DataFrame(), None
        
        if report_type == 'epic_report':
            report_df, error_msg = ReportGenerator.generate_epic_report(gl_service, args.get('scope_id'), args.get('epic_iid'))
        elif report_type == 'issue_analytics':
            report_df, error_msg = ReportGenerator.generate_issue_analytics_report(gl_service, args.get('scope_id'), args.get('scope_type'), created_after=args.get('start_date'), created_before=args.get('end_date'))
        elif report_type == 'defect_escape':
            report_df, error_msg = ReportGenerator.generate_defect_escape_report(
                gl_service, args.get('scope_id'), args.get('scope_type'), 
                args.get('start_date'), args.get('end_date'),
                args.get('qa_labels'), args.get('prod_labels')
            )
        
        if error_msg:
            flash(f"Error generating download: {error_msg}", "danger")
            return redirect(url_for('main.dashboard'))
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name=report_type)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"{report_type}_report.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        flash(f"An unexpected error occurred: {e}", "danger")
        return redirect(url_for('main.dashboard'))

@bp.route('/download_detailed_milestone_report')
def download_detailed_milestone_report():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    group_id, milestone_id = request.args.get('group_id'), request.args.get('milestone_id')
    if not group_id or not milestone_id:
        flash('Group ID and Milestone ID are required.', 'danger')
        return redirect(url_for('main.dashboard'))
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        report_html, error_msg = ReportGenerator.generate_detailed_milestone_report(gl_service, group_id, milestone_id)
        if error_msg:
            flash(f"Error generating report: {error_msg}", "danger")
            return redirect(url_for('main.dashboard'))
        output = io.BytesIO(report_html.encode('utf-8'))
        milestone = gl_service.get_single_milestone(group_id, milestone_id)
        return send_file(output, as_attachment=True, download_name=f"milestone_{milestone.title.replace(' ','_')}.html", mimetype='text/html')
    except Exception as e:
        flash(f"An unexpected error occurred: {e}", "danger")
        return redirect(url_for('main.dashboard'))
                            
@bp.route('/api/label_generator', methods=['POST'])
def label_generator():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    scope_id, scope_type, prefixes_str = data.get('scope_id'), data.get('scope_type'), data.get('prefixes')
    start_date, end_date = data.get('start_date'), data.get('end_date')

    if not all([scope_id, scope_type, prefixes_str, start_date, end_date]):
        return jsonify({'error': 'Scope, label prefixes, and date range are required.'}), 400
    
    prefixes = [p.strip() for p in prefixes_str.split(',') if p.strip()]
    if not prefixes:
        return jsonify({'error': 'Please provide at least one label prefix to check.'}), 400

    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        issues = gl_service.get_all_issues(scope_id=scope_id, scope_type=scope_type, state='opened', created_after=start_date, created_before=end_date)
        
        issues_with_suggestions = []
        for issue in issues:
            existing_labels = issue.labels
            missing_prefixes = [p for p in prefixes if not any(l.startswith(p) for l in existing_labels)]
            
            if missing_prefixes:
                suggestions = AutomationLogic.suggest_labels_scoped(issue, existing_labels)
                issue_dict = issue.asdict()
                issue_dict['suggestions'] = suggestions
                issues_with_suggestions.append(issue_dict)
        
        report_html = render_template('_label_suggestion_results.html', 
                                      issues=issues_with_suggestions,
                                      type_labels=current_app.config['TYPE_LABELS'],
                                      workflow_labels=current_app.config['WORKFLOW_LABELS'],
                                      priority_labels=current_app.config['PRIORITY_LABELS'])
        return jsonify({'html': report_html})
    except Exception as e:
        current_app.logger.error(f"Label generator failed: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'An internal error occurred: {e}'}), 500

@bp.route('/api/update_labels', methods=['POST'])
def update_labels():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    project_id, issue_iid, labels = data.get('project_id'), data.get('issue_iid'), data.get('labels')
    if not all([project_id, issue_iid, labels]):
        return jsonify({'error': 'Missing parameters for label update.'}), 400
    
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        success, error_msg = gl_service.update_issue_labels(project_id, issue_iid, labels)
        if success:
            return jsonify({'success': True, 'message': f'Labels for issue #{issue_iid} updated successfully!'})
        else:
            return jsonify({'success': False, 'message': f'Failed to update labels: {error_msg}'})
    except Exception as e:
        current_app.logger.error(f"Update labels failed: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': f'An internal error occurred: {e}'}), 500

@bp.route('/api/prd_to_story', methods=['POST'])
def prd_to_story():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    prd_text = data.get('prd_text')
    project_id = data.get('project_id')
    if not prd_text:
        return jsonify({'error': 'PRD text is required.'}), 400
    
    stories, error = AutomationLogic.generate_stories_from_prd(prd_text)
    
    if error:
        return jsonify({'error': error}), 500
    
    html = render_template('_prd_results.html', stories=stories, project_id=project_id)
    return jsonify({'html': html})

@bp.route('/api/create_issue', methods=['POST'])
def create_issue():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    project_id, title, description = data.get('project_id'), data.get('title'), data.get('description')
    if not all([project_id, title, description]):
        return jsonify({'error': 'Project ID, title, and description are required.'}), 400
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        issue, error = gl_service.create_issue(project_id, title, description)
        if error:
            return jsonify({'success': False, 'error': error})
        return jsonify({'success': True, 'issue_url': issue.web_url, 'issue_iid': issue.iid})
    except Exception as e:
        current_app.logger.error(f"Create issue failed: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': f'An internal error occurred: {e}'}), 500

@bp.route('/api/get_scope_data', methods=['POST'])
def get_scope_data():
    """Fetches milestones for the search filter dropdowns."""
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    scope_id, scope_type = data.get('scope_id'), data.get('scope_type')
    if not scope_id or not scope_type:
        return jsonify({'error': 'Scope ID and type are required.'}), 400
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        
        milestones_list = []
        if scope_type == 'group':
            milestones = gl_service.get_milestones(scope_id)
            milestones_list = [{'id': m.title, 'text': m.title} for m in milestones] if milestones else []

        return jsonify({'milestones': milestones_list})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
@bp.route('/api/search_issues', methods=['POST'])
def search_issues():
    """Handles the main issue search without pagination."""
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    
    gl_service = GitLabService(session['gitlab_url'], session['access_token'])
    
    search_params = {}
    if data.get('search_text'): search_params['search'] = data.get('search_text')
    
    if data.get('assignee'):
        assignees = [name.strip() for name in data.get('assignee').split(',') if name.strip()]
        if assignees: search_params['assignee_username'] = assignees
    if data.get('author'):
        authors = [name.strip() for name in data.get('author').split(',') if name.strip()]
        if authors: search_params['author_username'] = authors
        
    if data.get('milestone'): search_params['milestone'] = data.get('milestone')
    if data.get('labels'): search_params['labels'] = data.get('labels')

    all_issues = gl_service.get_all_issues(scope_id=data.get('scope_id'), scope_type=data.get('scope_type'), **search_params)
    
    issue_data = [{
        'iid': issue.iid, 'title': issue.title, 'web_url': issue.web_url,
        'assignee': issue.assignee['name'] if issue.assignee else 'None',
        'author': issue.author['name'], 'labels': ', '.join(issue.labels),
        'estimated_effort': ReportGenerator._convert_seconds_to_man_days(issue.time_stats.get('time_estimate', 0))
    } for issue in all_issues]
        
    html = render_template('_search_results.html', issues=issue_data)
    return jsonify({'html': html})

@bp.route('/download_search_results')
def download_search_results():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    args = request.args.to_dict()
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        
        search_params = {k: v for k, v in args.items() if k not in ['scope_id', 'scope_type'] and v}
        
        if 'assignee' in search_params:
            search_params['assignee_username'] = [name.strip() for name in search_params.pop('assignee').split(',')]
        if 'author' in search_params:
            search_params['author_username'] = [name.strip() for name in search_params.pop('author').split(',')]

        report_df, error_msg = ReportGenerator.generate_issue_analytics_report(
            gl_service, args.get('scope_id'), args.get('scope_type'), **search_params
        )
        if error_msg:
            flash(f"Error generating download: {error_msg}", "danger")
            return redirect(url_for('main.search_export_page'))

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name='search_results')
        output.seek(0)
        return send_file(output, as_attachment=True, download_name="issue_search_results.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        flash(f"An unexpected error occurred: {e}", "danger")
        return redirect(url_for('main.search_export_page'))

@bp.route('/api/search_users', methods=['POST'])
def search_users():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    search_term = request.json.get('search_term')
    if not search_term:
        return jsonify({'error': 'Search term is required'}), 400
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        users = gl_service.search_users(search_term)
        user_data = [{'id': u.username, 'text': f"{u.name} ({u.username})"} for u in users]
        return jsonify(user_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/get_user_activity', methods=['POST'])
def get_user_activity():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    username = data.get('username')
    time_period = data.get('time_period') # 'current' or 'last_week'
    
    if not username or not time_period:
        return jsonify({'error': 'Username and time period are required.'}), 400
        
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        
        mr_params = {'scope': 'all'}
        if time_period == 'current':
            mr_params['state'] = 'opened'
        elif time_period == 'last_week':
            last_week = datetime.utcnow() - timedelta(days=7)
            mr_params['updated_after'] = last_week.isoformat()

        mrs = gl_service.get_user_merge_requests(username, **mr_params)
        
        summary = {
            'total_mrs': len(mrs),
            'mrs_opened': sum(1 for mr in mrs if mr.state == 'opened'),
            'mrs_merged': sum(1 for mr in mrs if mr.state == 'merged'),
            'mrs_closed': sum(1 for mr in mrs if mr.state == 'closed'),
        }
        summary_html = render_template('_user_activity_summary.html', summary=summary, time_period=time_period)
        
        report_df, error_msg = ReportGenerator.generate_user_activity_report(gl_service, username, time_period)
        
        if error_msg:
            return jsonify({'error': error_msg}), 404

        table_html = render_template('_user_work_table.html', 
                                     issues=report_df.to_dict('records'),
                                     username=username,
                                     time_period=time_period)
                                     
        return jsonify({
            'summary_html': summary_html,
            'table_html': table_html
        })
    except Exception as e:
        current_app.logger.error(f"Error getting user activity: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'An internal error occurred: {e}'}), 500

@bp.route('/download_user_activity')
def download_user_activity():
    if not session.get('is_connected'): return redirect(url_for('main.index'))
    args = request.args
    username = args.get('username')
    time_period = args.get('time_period')
    
    if not username or not time_period:
        flash('Username and time period are required for download.', 'danger')
        return redirect(url_for('main.team_activity_page'))

    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        report_df, error_msg = ReportGenerator.generate_user_activity_report(gl_service, username, time_period)

        if error_msg:
            flash(f"Error generating download: {error_msg}", "danger")
            return redirect(url_for('main.team_activity_page'))

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            report_df.to_excel(writer, index=False, sheet_name=f'{username}_{time_period}_work')
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"{username}_{time_period}_activity.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        flash(f"An unexpected error occurred: {e}", "danger")
        return redirect(url_for('main.team_activity_page'))

@bp.route('/api/search_epics', methods=['POST'])
def search_epics():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    group_id = data.get('group_id')
    search_term = data.get('search_term')
    if not group_id or not search_term:
        return jsonify({'error': 'Group ID and search term are required'}), 400
    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        epics = gl_service.get_group_epics(group_id, search_term)
        epic_data = [{'id': e.iid, 'text': f"#{e.iid} - {e.title}"} for e in epics]
        return jsonify(epic_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/get_lead_cycle_time', methods=['POST'])
def get_lead_cycle_time():
    if not session.get('is_connected'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    scope_id = data.get('scope_id')
    scope_type = data.get('scope_type')
    start_date = data.get('start_date')
    end_date = data.get('end_date')

    if not all([scope_id, scope_type, start_date, end_date]):
        return jsonify({'error': 'Scope, start date, and end date are required.'}), 400

    try:
        gl_service = GitLabService(session['gitlab_url'], session['access_token'])
        scope_object = gl_service.get_scope_object(scope_id, scope_type)
        if not scope_object:
            return jsonify({'error': 'Scope not found or access denied.'}), 404

        full_path = scope_object.full_path
        
        metrics = gl_service.get_lead_cycle_time_metrics(full_path, scope_type, start_date, end_date)
        
        def format_time(seconds):
            if seconds is None:
                return "N/A"
            days = seconds / (60 * 60 * 24)
            return f"{days:.2f} days"

        return jsonify({
            'lead_time': format_time(metrics['lead_time']),
            'cycle_time': format_time(metrics['cycle_time'])
        })

    except Exception as e:
        current_app.logger.error(f"Lead/Cycle time query failed: {e}")
        return jsonify({'error': f'An internal error occurred: {e}'}), 500
