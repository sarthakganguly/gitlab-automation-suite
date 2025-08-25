# /app/main/logic.py
# Contains business logic for reports and automations.

from datetime import datetime, timedelta, timezone
import pandas as pd
from flask import current_app, render_template
from dateutil.relativedelta import relativedelta

class AutomationLogic:
    """Contains logic for automations."""
    @staticmethod
    def suggest_labels_scoped(issue, existing_labels):
        """Analyzes an issue to suggest a label for each missing scope."""
        scoped_suggestions = {}
        content = f"{issue.title.lower()} {issue.description.lower() if issue.description else ''}"

        has_type = any(l.startswith('type::') for l in existing_labels)
        has_workflow = any(l.startswith('workflow::') for l in existing_labels)
        has_priority = any(l.startswith('priority::') for l in existing_labels)

        if not has_type:
            if any(kw in content for kw in ["bug", "error", "fix", "issue", "problem", "failure"]):
                scoped_suggestions['type'] = "type::bug"
            elif any(kw in content for kw in ["feature", "implement", "add new", "create"]):
                scoped_suggestions['type'] = "type::new-feature"
            elif any(kw in content for kw in ["enhance", "improve", "update", "refine"]):
                scoped_suggestions['type'] = "type::enhancement"
            else:
                scoped_suggestions['type'] = "type::categorisation"

        if not has_workflow:
            if "blocked" in content or "waiting for" in content:
                scoped_suggestions['workflow'] = "workflow::blocked"
            elif "review" in content:
                scoped_suggestions['workflow'] = "workflow::review"
            elif "qa" in content or "test" in content:
                scoped_suggestions['workflow'] = "workflow::qa"
            else:
                scoped_suggestions['workflow'] = "workflow::triage"

        if not has_priority:
            if any(kw in content for kw in ["critical", "urgent", "blocker", "asap"]):
                scoped_suggestions['priority'] = "priority::1"
            elif any(kw in content for kw in ["low priority", "cosmetic"]):
                scoped_suggestions['priority'] = "priority::3"
            else:
                scoped_suggestions['priority'] = "priority::2"

        return scoped_suggestions
    
    @staticmethod
    def generate_stories_from_prd(prd_content):
        """Extracts user stories from PRD content based on simple rules."""
        user_stories = []
        sections = prd_content.split('\n\n')
        story_counter = 1
        for section in sections:
            if section.strip():
                if any(keyword in section.lower() for keyword in ['feature', 'requirement', 'user should', 'system should', 'must', 'shall']):
                    lines = section.split('\n')
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            story_description = f"As a user, I want to {line.lower()}" if not line.lower().startswith('as a') else line
                            user_stories.append({
                                'id': f"story_{story_counter}",
                                'title': f"User Story: {line[:50]}...",
                                'description': story_description
                            })
                            story_counter += 1
        
        if not user_stories:
            paragraphs = [p.strip() for p in prd_content.split('\n\n') if p.strip() and not p.strip().startswith('#')]
            for i, paragraph in enumerate(paragraphs[:10], 1):
                user_stories.append({
                    'id': f"story_{i}",
                    'title': f"User Story {i}",
                    'description': f"As a user, I want to implement: {paragraph[:200]}..."
                })
        
        return user_stories, None

class ReportGenerator:
    """Contains logic to generate reports."""
    @staticmethod
    def _convert_seconds_to_man_days(seconds):
        if not isinstance(seconds, (int, float)) or seconds == 0: return 0.0
        return round(seconds / (8 * 3600), 2)

    @staticmethod
    def _fetch_issues_with_or_labels(gl_service, labels_str, base_params):
        """Helper to fetch issues with an OR condition on labels."""
        all_issues = {}
        labels = [label.strip() for label in labels_str.split(',') if label.strip()]
        for label in labels:
            params = base_params.copy()
            params['labels'] = label
            issues = gl_service.get_all_issues(**params)
            for issue in issues:
                all_issues[issue.id] = issue
        return list(all_issues.values())

    @staticmethod
    def _calculate_escape_metrics(gl_service, scope_id, scope_type, start_date, end_date, qa_labels, prod_labels):
        """Centralized logic for calculating defect and dev escape rates."""
        created_params = {
            'scope_id': scope_id,
            'scope_type': scope_type,
            'created_after': start_date,
            'created_before': end_date
        }
        
        qa_issues = ReportGenerator._fetch_issues_with_or_labels(gl_service, qa_labels, created_params)
        prod_issues = ReportGenerator._fetch_issues_with_or_labels(gl_service, prod_labels, created_params)
        total_issues_created = gl_service.get_all_issues(**created_params)

        total_qa_bugs = len(qa_issues)
        total_prod_bugs = len(prod_issues)
        # Correctly calculate net total tickets for Dev Escape Rate
        total_tickets = len(total_issues_created) - total_qa_bugs

        qa_escape_ratio = (total_prod_bugs / total_qa_bugs) * 100 if total_qa_bugs > 0 else 0
        dev_escape_rate = (total_qa_bugs / total_tickets) * 100 if total_tickets > 0 else 0

        return {
            "total_qa_bugs": total_qa_bugs,
            "total_prod_bugs": total_prod_bugs,
            "total_tickets": total_tickets,
            "qa_escape_ratio": qa_escape_ratio,
            "dev_escape_rate": dev_escape_rate
        }

    @staticmethod
    def generate_defect_escape_report(gl_service, scope_id, scope_type, start_date, end_date, qa_labels, prod_labels):
        """Generates the Defect Escape Ratio report with OR logic for labels."""
        current_app.logger.info(f"Generating Defect Escape Report for {scope_type} {scope_id}")
        
        metrics = ReportGenerator._calculate_escape_metrics(
            gl_service, scope_id, scope_type, start_date, end_date, qa_labels, prod_labels
        )

        summary_data = {
            'Metric': [
                'Total QA Bugs created (in period)', 
                'Production Bugs (Escaped QA)', 
                'Defect Escape Ratio (%)',
                '---',
                "Total Tickets Created (net)",
                "Dev Escape Rate (%)"
            ],
            'Value': [
                metrics["total_qa_bugs"], 
                metrics["total_prod_bugs"], 
                f"{metrics['qa_escape_ratio']:.2f}",
                '---',
                metrics["total_tickets"],
                f"{metrics['dev_escape_rate']:.2f}"
            ]
        }
        report_df = pd.DataFrame(summary_data)
        current_app.logger.info("Defect Escape Report generated successfully.")
        return report_df, None

    @staticmethod
    def generate_defect_trend_report(gl_service, scope_id, scope_type, months, qa_labels, prod_labels):
        """Generates data for the defect escape trend graph."""
        current_app.logger.info(f"Generating Defect Trend Report for {months} months.")
        
        labels = []
        defect_escape_ratios = []
        dev_escape_rates = []
        total_qa_bugs_list = []
        total_prod_bugs_list = []
        total_tickets_list = []
        
        today = datetime.now(timezone.utc)
        current_month_start = today.replace(day=1)

        for i in range(int(months), 0, -1):
            end_of_month = current_month_start - relativedelta(months=i-1)
            start_of_month = current_month_start - relativedelta(months=i)
            
            month_label = start_of_month.strftime("%Y-%m")
            labels.append(month_label)

            metrics = ReportGenerator._calculate_escape_metrics(
                gl_service, scope_id, scope_type, 
                start_of_month.strftime('%Y-%m-%d'), 
                end_of_month.strftime('%Y-%m-%d'), 
                qa_labels, prod_labels
            )
            
            defect_escape_ratios.append(round(metrics['qa_escape_ratio'], 2))
            dev_escape_rates.append(round(metrics['dev_escape_rate'], 2))
            total_qa_bugs_list.append(metrics['total_qa_bugs'])
            total_prod_bugs_list.append(metrics['total_prod_bugs'])
            total_tickets_list.append(metrics['total_tickets'])

        chart_data = {
            'labels': labels,
            'defect_escape_ratios': defect_escape_ratios,
            'dev_escape_rates': dev_escape_rates,
            'total_qa_bugs': total_qa_bugs_list,
            'total_prod_bugs': total_prod_bugs_list,
            'total_tickets': total_tickets_list
        }
        return chart_data, None

    @staticmethod
    def generate_epic_report(gl_service, group_id, epic_iid):
        """Generates a report for a specific epic."""
        issues = gl_service.get_epic_issues(group_id, epic_iid)
        if not issues:
            return pd.DataFrame(), "No issues found for this epic."

        sorted_issues = sorted(issues, key=lambda i: i.created_at, reverse=True)

        report_data = []
        for issue in sorted_issues:
            assignees = ', '.join([a['name'] for a in issue.assignees]) if issue.assignees else 'Unassigned'
            status = next((l.split('::')[1] for l in issue.labels if l.startswith('workflow::')), "NA")
            milestone_date = issue.milestone['due_date'] if issue.milestone and 'due_date' in issue.milestone else 'NA'
            
            path_with_namespace = issue.references['full'].split('#')[0]
            issue_url_display = f"{path_with_namespace}#{issue.iid}"

            report_data.append({
                'Task': issue.title,
                'Assignees': assignees,
                'Status': status,
                'Created': pd.to_datetime(issue.created_at).strftime('%Y-%m-%d'),
                'Milestone Date': milestone_date,
                'issue_url': issue.web_url,
                'issue_url_display': issue_url_display
            })
        
        df = pd.DataFrame(report_data)
        return df, None

    @staticmethod
    def generate_issue_analytics_report(gl_service, scope_id, scope_type, **kwargs):
        """Generates the Issue Analytics report."""
        issues = gl_service.get_all_issues(scope_id=scope_id, scope_type=scope_type, **kwargs)
        if not issues: return pd.DataFrame(), "No issues found."
        issue_data, project_name_cache = [], {}
        for issue in issues:
            issue_dict = issue.asdict()
            project_id = issue_dict['project_id']
            if project_id not in project_name_cache:
                try: project_name_cache[project_id] = gl_service.gl.projects.get(project_id).name_with_namespace
                except: project_name_cache[project_id] = "Unknown"
            time_stats = issue_dict.get('time_stats', {})
            effort_days = max(ReportGenerator._convert_seconds_to_man_days(time_stats.get('time_estimate', 0)),
                              ReportGenerator._convert_seconds_to_man_days(time_stats.get('total_time_spent', 0)))
            issue_type = next((l.split('::')[1] for l in issue_dict.get('labels', []) if l.startswith('type::')), "NA")
            issue_data.append({
                'IID': issue_dict.get('iid'), 'Type': issue_type, 'Title': issue_dict.get('title'),
                'State': issue_dict.get('state'), 'URL': issue_dict.get('web_url'), 'Project': project_name_cache[project_id],
                'Created At': pd.to_datetime(issue_dict.get('created_at')).strftime('%Y-%m-%d'),
                'Updated At': pd.to_datetime(issue_dict.get('updated_at')).strftime('%Y-%m-%d'),
                'Closed At': pd.to_datetime(issue_dict.get('closed_at')).strftime('%Y-%m-%d') if issue_dict.get('closed_at') else '',
                'Due Date': issue_dict.get('due_date', ''), 'Labels': ', '.join(issue_dict.get('labels', [])),
                'Milestone': issue_dict.get('milestone', {}).get('title', '') if issue_dict.get('milestone') else '',
                'Assignee': issue_dict.get('assignee', {}).get('name', 'N/A') if issue_dict.get('assignee') else 'N/A',
                'Assignees': ', '.join([a['name'] for a in issue_dict.get('assignees', [])]),
                'Author': issue_dict.get('author', {}).get('name', 'N/A'),
                'Effort (Man Days)': effort_days, 'Weight': issue_dict.get('weight')
            })
        return pd.DataFrame(issue_data), None
    
    @staticmethod
    def generate_milestone_list(gl_service, group_id, start_date, end_date):
        """Generates a list of milestones with issue counts."""
        milestones = gl_service.get_milestones(group_id)
        if not milestones: return [], "No milestones found for this group."
        milestone_data = []
        start_dt, end_dt = pd.to_datetime(start_date), pd.to_datetime(end_date)
        for m in milestones:
            m_dict = m.asdict()
            due_date_str = m_dict.get('due_date')
            if due_date_str and (start_dt <= pd.to_datetime(due_date_str) <= end_dt):
                issues = gl_service.get_all_issues(scope_id=group_id, scope_type='group', milestone=m_dict['title'])
                milestone_data.append({
                    'id': m_dict['id'], 'title': m_dict['title'], 'due_date': due_date_str,
                    'total_issues': len(issues), 'closed_issues': sum(1 for i in issues if i.state == 'closed')
                })
        return milestone_data, None

    @staticmethod
    def generate_detailed_milestone_report(gl_service, group_id, milestone_id):
        """Generates a detailed HTML report for a single milestone."""
        milestone = gl_service.get_single_milestone(group_id, milestone_id)
        if not milestone: return None, "Milestone not found."
        milestone_data = milestone.asdict()
        issues = gl_service.get_all_issues(scope_id=group_id, scope_type='group', milestone=milestone.title)
        total_issues = len(issues)
        closed_issues = sum(1 for i in issues if i.state == 'closed')
        stats = {
            'completion_percentage': round((closed_issues / total_issues * 100) if total_issues > 0 else 0, 2),
            'start_date': milestone_data.get('start_date', 'N/A'), 'due_date': milestone_data.get('due_date', 'N/A'),
            'total_issues': total_issues, 'closed_issues': closed_issues, 'open_issues': total_issues - closed_issues
        }
        issue_rows = [{'iid': i.iid, 'title': i.title, 'state': i.state, 'url': i.web_url} for i in issues]
        start_date_str, due_date_str = milestone_data.get('start_date'), milestone_data.get('due_date')
        burndown_data = {'labels': [], 'ideal': [], 'actual': []}
        if start_date_str and due_date_str:
            try:
                start_date, due_date = datetime.strptime(start_date_str, '%Y-%m-%d'), datetime.strptime(due_date_str, '%Y-%m-%d')
                if due_date >= start_date:
                    date_range = pd.date_range(start=start_date, end=due_date)
                    burndown_data['labels'] = [d.strftime('%Y-%m-%d') for d in date_range]
                    total_days = (due_date - start_date).days
                    daily_burn_rate = total_issues / total_days if total_days > 0 else total_issues
                    burndown_data['ideal'] = [max(0, total_issues - (i * daily_burn_rate)) for i in range(total_days + 1)]
                    closed_issues_on_date = {d.strftime('%Y-%m-%d'): 0 for d in date_range}
                    for issue in issues:
                        if issue.state == 'closed' and issue.closed_at:
                            closed_date_str = datetime.fromisoformat(issue.closed_at.replace('Z', '+00:00')).strftime('%Y-%m-%d')
                            if closed_date_str in closed_issues_on_date: closed_issues_on_date[closed_date_str] += 1
                    cumulative_closed = 0
                    actual_burn = []
                    for day_str in burndown_data['labels']:
                        cumulative_closed += closed_issues_on_date.get(day_str, 0)
                        actual_burn.append(total_issues - cumulative_closed)
                    burndown_data['actual'] = actual_burn
            except Exception as e:
                current_app.logger.error(f"Could not generate burndown chart data: {e}")
                burndown_data = {'labels': ['Start', 'End'], 'ideal': [total_issues, 0], 'actual': [total_issues, stats['open_issues']]}
        else:
           burndown_data = {'labels': ['Start', 'Current'], 'ideal': [total_issues, 0], 'actual': [total_issues, stats['open_issues']]}
        return render_template('_detailed_milestone_report.html', milestone=milestone_data, stats=stats, issues=issue_rows, burndown_data=burndown_data), None

    @staticmethod
    def generate_user_activity_report(gl_service, username, time_period):
        """Generates an activity report for a specific user."""
        params = {'assignee_username': username, 'scope': 'all'}
        if time_period == 'current':
            params['state'] = 'opened'
        elif time_period == 'last_week':
            last_week = datetime.utcnow() - timedelta(days=7)
            params['updated_after'] = last_week.isoformat()

        issues = gl_service.get_all_issues(**params)
        
        if not issues:
            return pd.DataFrame(), "No issues found for this user in the specified period."

        issue_data = []
        for issue in issues:
            issue_dict = issue.asdict()
            time_stats = issue_dict.get('time_stats', {})
            
            workflow_status = next((l for l in issue.labels if l.startswith('workflow::')), "NA")
            type_status = next((l for l in issue.labels if l.startswith('type::')), "type::other")

            issue_data.append({
                'issue_iid': issue.iid,
                'issue_title': issue.title,
                'issue_workflow_status': workflow_status.split('::')[-1],
                'type_scoped_status': type_status.split('::')[-1],
                'issue_created_date': pd.to_datetime(issue.created_at).strftime('%Y-%m-%d'),
                'issue_updated_date': pd.to_datetime(issue.updated_at).strftime('%Y-%m-%d'),
                'estimated_efforts': ReportGenerator._convert_seconds_to_man_days(time_stats.get('time_estimate', 0)),
                'web_url': issue.web_url
            })
        
        df = pd.DataFrame(issue_data)
        return df, None
