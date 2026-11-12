"""WSGI entry point for production servers such as Gunicorn."""
from app import app, initialize_database

initialize_database()
