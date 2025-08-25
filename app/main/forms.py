# /app/main/forms.py
# Defines the WTForms used in the application.

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, URL

class ConnectionForm(FlaskForm):
    """Form for GitLab connection details."""
    gitlab_url = StringField('GitLab URL', validators=[DataRequired(), URL()], default='https://gitlab.com')
    access_token = PasswordField('GitLab Personal Access Token', validators=[DataRequired()])
    submit = SubmitField('Connect')
