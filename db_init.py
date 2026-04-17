"""
Run once to create all database tables.

Usage:
    python db_init.py

For a fresh Replit project or any environment without Flask-Migrate set up,
this is the quickest way to get the schema in place.
"""
import os
from app import create_app
from app.extensions import db
from app.models import User, Portfolio, AssetHolding, AnalysisRun, AnalysisResult  # noqa: ensure models registered

app = create_app(os.environ.get('FLASK_ENV', 'default'))

with app.app_context():
    db.create_all()
    from sqlalchemy import inspect
    tables = inspect(db.engine).get_table_names()
    print('All tables created:', tables)
