import os

# Superset configuration
SECRET_KEY = os.environ.get('SUPERSET_SECRET_KEY', 'supersecretkey')

# Database configuration - use PostgreSQL
SQLALCHEMY_DATABASE_URI = os.environ.get(
    'SQLALCHEMY_DATABASE_URI',
    'postgresql+psycopg2://biz:biz_password@biz-db:5432/superset'
)

# Disable examples
SUPERSET_LOAD_EXAMPLES = False

# Flask-WTF flag for CSRF
WTF_CSRF_ENABLED = True
# Add endpoints that need to be exempt from CSRF protection
WTF_CSRF_EXEMPT_LIST = []
WTF_CSRF_TIME_LIMIT = None

# Set this API key to enable Mapbox visualizations
MAPBOX_API_KEY = os.environ.get('MAPBOX_API_KEY', '')

# Enable feature flags
FEATURE_FLAGS = {
    'ALERTS_ATTACH_REPORTS': True,
    'DASHBOARD_NATIVE_FILTERS': True,
    'DASHBOARD_CROSS_FILTERS': True,
    'DASHBOARD_FILTERS_EXPERIMENTAL': True,
}

# Limit of queries to display in the query history
QUERY_HISTORY_LIMIT = 1000

# Enable scheduled queries
SCHEDULED_QUERIES = {
    'ENABLE': True,
}
