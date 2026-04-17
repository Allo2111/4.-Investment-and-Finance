from flask import Flask
from .extensions import db, login_manager, migrate, csrf
from config import config


def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Extensions
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    # User loader
    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Blueprints
    from .auth.routes import auth_bp
    from .portfolios.routes import portfolios_bp
    from .analysis.routes import analysis_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(portfolios_bp)
    app.register_blueprint(analysis_bp)

    return app
