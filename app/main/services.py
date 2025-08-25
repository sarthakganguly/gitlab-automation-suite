# /app/main/services.py
# Handles all interactions with the GitLab API.

import gitlab
import requests
from flask import current_app
import urllib3
import re
import json

# Suppress the InsecureRequestWarning from urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class GitLabService:
    """
    Service class to handle all interactions with the GitLab API.
    It is initialized on-demand with credentials from the user's session.
    """
    def __init__(self, gitlab_url, private_token):
        # Normalize the URL to remove any trailing slashes
        self.gitlab_url = gitlab_url.rstrip('/')
        self.private_token = private_token
        self.gl = None
        try:
            self.gl = gitlab.Gitlab(self.gitlab_url, private_token=self.private_token, ssl_verify=False, timeout=10) 
            self.gl.auth()
            current_app.logger.info(f"Successfully connected to GitLab at {self.gitlab_url}")
        except (gitlab.exceptions.GitlabAuthenticationError, requests.exceptions.RequestException) as e:
            current_app.logger.error(f"GitLab connection failed: {e}")
            raise ConnectionError(f"Failed to connect or authenticate with GitLab. Details: {e}") from e

    def execute_graphql(self, query, variables=None):
        """Executes a GraphQL query by substituting variables directly into the query string."""
        graphql_url = f"{self.gitlab_url}/api/graphql"
        headers = {
            'Authorization': f'Bearer {self.private_token}',
            'Content-Type': 'application/json'
        }

        # Substitute variables directly into the query string
        if variables:
            for key, value in variables.items():
                # GraphQL requires string literals to be in double quotes.
                # We use json.dumps to handle proper escaping of the string.
                query = query.replace(f'${key}', json.dumps(value))
        
        # Remove the variable definition part of the query, e.g., ($fullPath: ID!, ...)
        final_query = re.sub(r'query(\s+\w+)?\s*\([^)]*\)', 'query', query)
        
        # Remove newlines and extra whitespace to create a clean, single-line query
        final_query = " ".join(final_query.split())

        payload = {'query': final_query}

        try:
            current_app.logger.info(f"Attempting GraphQL POST to: {graphql_url}")
            current_app.logger.info(f"GraphQL Payload: {payload}")
            
            response = requests.post(graphql_url, headers=headers, json=payload, verify=False)
            response.raise_for_status()  # Raises an exception for bad status codes (4xx or 5xx)
            
            response_data = response.json()
            current_app.logger.info(f"GraphQL Response: {json.dumps(response_data)}")
            
            return response_data

        except requests.exceptions.HTTPError as e:
            current_app.logger.error(f"GraphQL query failed with status code: {e.response.status_code}")
            current_app.logger.error(f"GraphQL error response: {e.response.text}")
            raise Exception(f"GraphQL query failed: {e.response.text}")
        except Exception as e:
            current_app.logger.error(f"An unexpected error occurred during GraphQL request: {e}")
            raise e

    def get_lead_cycle_time_metrics(self, scope_full_path, scope_type, start_date, end_date):
        """Fetches Lead and Cycle time by first finding the value stream and then querying its stages."""
        
        # Step 1: Get the first available value stream for the scope
        value_stream_query = f"""
            query GetValueStreams($fullPath: ID!) {{
              {scope_type}(fullPath: $fullPath) {{
                valueStreams {{
                  nodes {{
                    id
                    name
                  }}
                }}
              }}
            }}
        """
        vs_vars = {"fullPath": scope_full_path}
        vs_result = self.execute_graphql(value_stream_query, vs_vars)
        
        value_streams = vs_result.get('data', {}).get(scope_type, {}).get('valueStreams', {}).get('nodes', [])
        if not value_streams:
            raise Exception("No value streams found for this scope.")
            
        value_stream_id = value_streams[0]['id'] # Use the first value stream
        current_app.logger.info(f"Found value stream '{value_streams[0]['name']}' with ID: {value_stream_id}")

        # Step 2: Get the metrics for that value stream's stages
        metrics_query = """
            query GetAllStageMetrics($fullPath: ID!, $vsId: [AnalyticsCycleAnalyticsValueStreamID!], $startDate: Date!, $endDate: Date!) {
              %s(fullPath: $fullPath) {
                valueStreams(ids: $vsId) {
                  nodes {
                    stages {
                      nodes {
                        id
                        name
                        metrics(timeframe: { start: $startDate, end: $endDate }) {
                          median {
                            value
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
        """ % scope_type
        
        metric_vars = {
            "fullPath": scope_full_path,
            "vsId": [value_stream_id],
            "startDate": start_date,
            "endDate": end_date
        }
        
        metrics_result = self.execute_graphql(metrics_query, metric_vars)
        
        # Navigate through the corrected structure
        vs_nodes = metrics_result.get('data', {}).get(scope_type, {}).get('valueStreams', {}).get('nodes', [])
        if not vs_nodes:
            raise Exception("Could not retrieve metrics for the value stream.")

        stages = vs_nodes[0].get('stages', {}).get('nodes', [])

        lead_time_seconds = None
        cycle_time_seconds = None

        for stage in stages:
            if stage['name'].lower() == 'lead time':
                lead_time_seconds = stage.get('metrics', [{}])[0].get('median', {}).get('value')
            if stage['name'].lower() == 'cycle time':
                cycle_time_seconds = stage.get('metrics', [{}])[0].get('median', {}).get('value')
        
        return {
            "lead_time": lead_time_seconds,
            "cycle_time": cycle_time_seconds
        }

    def get_scope_object(self, scope_id, scope_type):
        """Gets a group or project object by its ID and ensures it has a 'full_path' attribute."""
        try:
            if scope_type == 'group':
                return self.gl.groups.get(scope_id)
            else:
                project = self.gl.projects.get(scope_id)
                project.full_path = project.path_with_namespace
                return project
        except gitlab.exceptions.GitlabGetError:
            return None

    def search_users(self, search_term):
        """Searches for users by name or username."""
        try:
            return self.gl.users.list(search=search_term)
        except Exception as e:
            current_app.logger.error(f"Error searching for users: {e}")
            return []

    def get_user_merge_requests(self, username, **kwargs):
        """Fetches merge requests for a specific user."""
        try:
            return self.gl.mergerequests.list(author_username=username, all=True, **kwargs)
        except Exception as e:
            current_app.logger.error(f"Error fetching merge requests for user {username}: {e}")
            return []

    def get_user_groups(self):
        """Fetches only the top-level groups the user is a member of."""
        try:
            return self.gl.groups.list(all=True, as_list=True, top_level_only=True)
        except Exception as e:
            current_app.logger.error(f"Failed to fetch user groups: {e}")
            return None

    def get_group_details(self, group_id):
        """Fetches details for a single group, including subgroups and projects."""
        try:
            group = self.gl.groups.get(group_id)
            subgroups = group.subgroups.list(all=True)
            projects = group.projects.list(all=True, include_subgroups=False)
            return {'group': group, 'subgroups': subgroups, 'projects': projects}
        except gitlab.exceptions.GitlabGetError as e:
            current_app.logger.error(f"Could not find group with ID {group_id}: {e}")
            return None
        except Exception as e:
            current_app.logger.error(f"Error fetching details for group {group_id}: {e}")
            return None

    def get_group_epics(self, group_id, search_term):
        """Searches for epics within a group."""
        try:
            group = self.gl.groups.get(group_id)
            return group.epics.list(search=search_term)
        except Exception as e:
            current_app.logger.error(f"Error searching epics in group {group_id}: {e}")
            return []

    def _get_descendant_epic_issues(self, group, epic_iid, issues_list):
        """Recursively fetches issues from an epic and its descendants."""
        try:
            epic = group.epics.get(epic_iid)
            issues_list.extend(epic.issues.list(all=True, as_list=True))
            descendants = epic.epics.list(all=True, as_list=True)
            for descendant in descendants:
                self._get_descendant_epic_issues(group, descendant.iid, issues_list)
        except Exception as e:
            current_app.logger.error(f"Could not process epic iid {epic_iid} in group {group.id}: {e}")

    def get_epic_issues(self, group_id, epic_iid):
        """Fetches all issues for a given epic and its descendant epics."""
        try:
            group = self.gl.groups.get(group_id)
            all_issues = []
            self._get_descendant_epic_issues(group, epic_iid, all_issues)
            unique_issues = list({issue.id: issue for issue in all_issues}.values())
            current_app.logger.info(f"Found {len(unique_issues)} unique issues for epic #{epic_iid} and its descendants.")
            return unique_issues
        except Exception as e:
            current_app.logger.error(f"Error fetching issues for epic {epic_iid}: {e}")
            return []

    def get_issues(self, scope_id=None, scope_type=None, **kwargs):
        """
        Fetches issues with filters. Returns a paginated object by default.
        Can be scoped to a group/project or be instance-wide.
        """
        try:
            if scope_id and scope_type:
                if scope_type == 'group':
                    item = self.gl.groups.get(scope_id)
                    kwargs['include_subgroups'] = True
                else: # project
                    item = self.gl.projects.get(scope_id)
                issues = item.issues.list(**kwargs)
            else: # Instance-wide search
                issues = self.gl.issues.list(**kwargs)
            
            current_app.logger.info(f"Fetched paginated issues with filters: {kwargs}")
            return issues
        except Exception as e:
            current_app.logger.error(f"Error fetching paginated issues: {e}")
            return []
    
    def get_all_issues(self, scope_id=None, scope_type=None, **kwargs):
        """
        Fetches ALL issues as a list, without pagination.
        Can be scoped to a group/project or be instance-wide.
        """
        try:
            kwargs['all'] = True
            if scope_id and scope_type:
                if scope_type == 'group':
                    item = self.gl.groups.get(scope_id)
                    kwargs['include_subgroups'] = True
                else:
                    item = self.gl.projects.get(scope_id)
                issues = item.issues.list(**kwargs)
            else: # Instance-wide search
                issues = self.gl.issues.list(**kwargs)
            
            if 'as_list' not in kwargs or kwargs['as_list']:
                 issues = list(issues)

            current_app.logger.info(f"Fetched ALL {len(issues)} issues with filters: {kwargs}")
            return issues
        except Exception as e:
            current_app.logger.error(f"Error fetching all issues: {e}")
            return []
            
    def get_scope_members(self, scope_id, scope_type='group'):
        """Fetches all members of a group or project."""
        try:
            if scope_type == 'group':
                item = self.gl.groups.get(scope_id)
            else:
                item = self.gl.projects.get(scope_id)
            return item.members.list(all=True)
        except Exception as e:
            current_app.logger.error(f"Error fetching members for {scope_type} ID {scope_id}: {e}")
            return []
    
    def get_milestones(self, group_id, **kwargs):
        """Fetches milestones for a given group."""
        try:
            group = self.gl.groups.get(group_id)
            return group.milestones.list(all=True, as_list=True, **kwargs)
        except Exception as e:
            current_app.logger.error(f"Error fetching milestones for group {group_id}: {e}")
            return []
    
    def get_single_milestone(self, group_id, milestone_id):
        """Fetches a single milestone by its ID."""
        try:
            group = self.gl.groups.get(group_id)
            return group.milestones.get(milestone_id)
        except Exception as e:
            current_app.logger.error(f"Error fetching single milestone {milestone_id} from group {group_id}: {e}")
            return None
    
    def update_issue_labels(self, project_id, issue_iid, labels_to_add):
        """Updates the labels for a specific issue."""
        try:
            project = self.gl.projects.get(project_id, lazy=True)
            issue = project.issues.get(issue_iid, lazy=True)
            issue.labels = list(set(issue.labels + labels_to_add))
            issue.save()
            current_app.logger.info(f"Successfully updated labels for issue {project_id}/{issue_iid}")
            return True, None
        except gitlab.exceptions.GitlabError as e:
            current_app.logger.error(f"Failed to update labels for issue {project_id}/{issue_iid}: {e}")
            return False, str(e)

    def create_issue(self, project_id, title, description):
        """Creates a new issue in a project."""
        try:
            project = self.gl.projects.get(project_id)
            issue = project.issues.create({'title': title, 'description': description})
            current_app.logger.info(f"Created issue #{issue.iid} in project {project_id}")
            return issue, None
        except gitlab.exceptions.GitlabError as e:
            current_app.logger.error(f"Failed to create issue in project {project_id}: {e}")
            return None, str(e)
