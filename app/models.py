from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from .extensions import db

# ── Asset class definitions ────────────────────────────────

ASSET_CLASSES = [
    ('equity',           'Public Equity'),
    ('etf',              'ETF / Fund'),
    ('cash',             'Cash'),
    ('bond',             'Bond / Fixed Income'),
    ('real_estate',      'Real Estate'),
    ('commodity',        'Commodity / Gold'),
    ('private_business', 'Private Business'),
    ('retirement',       'Retirement / MPF / Pension'),
    ('liability',        'Liability / Debt'),
    ('other',            'Other'),
]

ASSET_CLASS_LABELS = dict(ASSET_CLASSES)

# These asset classes can have live prices fetched via yfinance
LISTED_CLASSES = {'equity', 'etf', 'bond', 'commodity'}


# ── User ──────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    portfolios = db.relationship('Portfolio', backref='user', lazy='dynamic',
                                  cascade='all, delete-orphan')
    analysis_runs = db.relationship('AnalysisRun', backref='user', lazy='dynamic',
                                     cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'


# ── Portfolio ─────────────────────────────────────────────

class Portfolio(db.Model):
    __tablename__ = 'portfolios'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    base_currency = db.Column(db.String(10), default='USD')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset_holdings = db.relationship('AssetHolding', backref='portfolio', lazy='dynamic',
                                      cascade='all, delete-orphan',
                                      order_by='AssetHolding.asset_class, AssetHolding.name')
    analysis_runs = db.relationship('AnalysisRun', backref='portfolio', lazy='dynamic',
                                     cascade='all, delete-orphan',
                                     order_by='AnalysisRun.created_at.desc()')

    def __repr__(self):
        return f'<Portfolio {self.name}>'


# ── AssetHolding ──────────────────────────────────────────

class AssetHolding(db.Model):
    """
    Unified holding model supporting both listed (quantity-based) and
    manual-value assets across 9 asset classes.

    Listed assets  (equity, etf, bond, commodity):
        value = quantity * current_price  (or avg_cost as fallback)

    Manual assets  (cash, real_estate, private_business, retirement, liability, other):
        value = manual_value
    """
    __tablename__ = 'asset_holdings'

    id = db.Column(db.Integer, primary_key=True)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolios.id', ondelete='CASCADE'),
                              nullable=False)

    # Classification
    asset_class = db.Column(db.String(50), nullable=False, default='equity')
    asset_subclass = db.Column(db.String(100), nullable=True)

    # Identity
    symbol = db.Column(db.String(20), nullable=True)    # exchange ticker for listed assets
    name = db.Column(db.String(255), nullable=False)    # human-readable name / display label

    # Listed-asset fields
    is_listed = db.Column(db.Boolean, default=False)
    quantity = db.Column(db.Float, nullable=True)
    avg_cost = db.Column(db.Float, nullable=True)       # per-unit cost basis
    current_price = db.Column(db.Float, nullable=True)  # last fetched price

    # Value fields
    market_value = db.Column(db.Float, nullable=True)   # auto-computed: qty * price
    manual_value = db.Column(db.Float, nullable=True)   # user-supplied for non-listed

    # Context
    currency = db.Column(db.String(10), default='USD')
    geography = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Computed properties ───────────────────────────────

    @property
    def effective_value(self):
        """Best-estimate current value in the holding's own currency."""
        if self.is_listed:
            if self.quantity and self.current_price:
                return round(self.quantity * self.current_price, 2)
            if self.market_value:
                return self.market_value
            if self.quantity and self.avg_cost:
                return round(self.quantity * self.avg_cost, 2)  # cost-basis fallback
            return None
        return self.manual_value

    @property
    def asset_class_label(self):
        return ASSET_CLASS_LABELS.get(self.asset_class, self.asset_class)

    @property
    def is_liability(self):
        return self.asset_class == 'liability'

    @property
    def display_name(self):
        if self.symbol and self.name and self.symbol != self.name:
            return f'{self.symbol} — {self.name}'
        return self.name or self.symbol or '—'

    def __repr__(self):
        return f'<AssetHolding {self.name} ({self.asset_class})>'


# ── AnalysisRun ───────────────────────────────────────────

class AnalysisRun(db.Model):
    __tablename__ = 'analysis_runs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolios.id', ondelete='CASCADE'),
                              nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, running, complete, failed
    summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    results = db.relationship('AnalysisResult', backref='run', lazy='dynamic',
                               cascade='all, delete-orphan')

    def __repr__(self):
        return f'<AnalysisRun {self.id} [{self.status}]>'


# ── AnalysisResult ────────────────────────────────────────

class AnalysisResult(db.Model):
    __tablename__ = 'analysis_results'

    id = db.Column(db.Integer, primary_key=True)
    analysis_run_id = db.Column(db.Integer, db.ForeignKey('analysis_runs.id', ondelete='CASCADE'),
                                 nullable=False)
    module_name = db.Column(db.String(100), nullable=False)
    payload_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<AnalysisResult {self.module_name}>'
