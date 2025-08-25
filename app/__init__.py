# /app/__init__.py
# Application factory and package setup.

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask
from config import Config

def create_app(config_class=Config):
    """Creates and configures the Flask application."""
    # Explicitly define the template folder relative to the app's instance path.
    app = Flask(__name__, instance_relative_config=True, template_folder='templates')
    app.config.from_object(config_class)

    # --- Logging Setup ---
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join('logs', f'app_run_{timestamp}.log')
    
    file_handler = RotatingFileHandler(log_file, maxBytes=10240, backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('GitLab Analytics Hub startup')

    # --- Enhanced Debug Logging for Template Pathing ---
    app.logger.info(f"--- PATH DEBUGGING ---")
    app.logger.info(f"App Root Path: {app.root_path}")
    app.logger.info(f"App Template Folder: {app.template_folder}")
    app.logger.info(f"Full Template Path: {os.path.join(app.root_path, app.template_folder)}")
    app.logger.info(f"--- END PATH DEBUGGING ---")

    # --- Register Blueprints ---
    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    # --- Log Registered Blueprints for Debugging ---
    app.logger.info("--- BLUEPRINT DEBUGGING ---")
    for bp_name, blueprint in app.blueprints.items():
        app.logger.info(f"Registered Blueprint: '{bp_name}'")
        app.logger.info(f"  - Blueprint Template Folder: {blueprint.template_folder}")
    app.logger.info("--- END BLUEPRINT DEBUGGING ---")

    return app
