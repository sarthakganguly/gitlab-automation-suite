# /config.py
# Application configuration settings.

import os

class Config:
    """Application configuration settings."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'a-very-secret-and-secure-key-for-dev')
    CHATGPT_API_KEY = os.environ.get('CHATGPT_API_KEY') 

    # Add these two lines for your internal LiteLLM gateway
    LITELLM_GATEWAY_URL = "https://prism-api.hinagro.com/gateway"
    LITELLM_GATEWAY_KEY = "sk-bjQv4QrTcUd9Qh8HlXJ_Rw"
    
    # Predefined Labels
    WORKFLOW_LABELS = [
        "workflow::grooming", "workflow::scoping", "workflow::clarification",
        "workflow::qa-scoping", "workflow::triage", "workflow::review",
        "workflow::qa", "workflow::resolved", "workflow::hold", "workflow::blocked"
    ]
    TYPE_LABELS = [
        "type::new-feature", "type::customisation", "type::enhancement", "type::bug",
        "type::categorisation", "type::refactoring", "type::dc-movement",
        "type::move-to-prod", "type::operations", "type::poc", "type::other"
    ]
    PRIORITY_LABELS = ["priority::1", "priority::2", "priority::3"]