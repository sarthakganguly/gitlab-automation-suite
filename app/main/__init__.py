# /app/main/__init__.py
from flask import Blueprint

# Explicitly define the template_folder relative to this blueprint's location.
# This tells Flask to look in the 'app/templates' directory directly.
bp = Blueprint('main', __name__, template_folder='../templates')

from app.main import routes, forms, services, logic
