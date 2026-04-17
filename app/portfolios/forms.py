from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, FloatField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, NumberRange
from ..models import ASSET_CLASSES

CURRENCY_CHOICES = [
    ('USD', 'USD'), ('HKD', 'HKD'), ('GBP', 'GBP'),
    ('EUR', 'EUR'), ('JPY', 'JPY'), ('CNY', 'CNY'),
    ('AUD', 'AUD'), ('SGD', 'SGD'),
]


class PortfolioForm(FlaskForm):
    name = StringField('Portfolio Name', validators=[DataRequired(), Length(max=255)])
    description = TextAreaField('Description (optional)', validators=[Optional(), Length(max=1000)])
    base_currency = SelectField('Base Currency', choices=CURRENCY_CHOICES, default='USD')
    submit = SubmitField('Save Portfolio')


class AssetHoldingForm(FlaskForm):
    """
    Single form covering all asset classes.
    JS on the frontend shows/hides fields based on asset_class selection.
    Server-side validation is lenient — different classes require different fields.
    """
    asset_class = SelectField('Asset Class', choices=ASSET_CLASSES,
                               validators=[DataRequired()])

    # Identity
    symbol = StringField('Ticker / Symbol', validators=[Optional(), Length(max=20)])
    name = StringField('Name / Label', validators=[DataRequired(), Length(max=255)])

    # Listed fields
    quantity = FloatField('Quantity (shares / units)', validators=[Optional(), NumberRange(min=0)])
    avg_cost = FloatField('Average Cost (per unit)', validators=[Optional(), NumberRange(min=0)])

    # Manual-value fields
    manual_value = FloatField('Current Value', validators=[Optional(), NumberRange(min=0)])

    # Shared
    currency = SelectField('Currency', choices=CURRENCY_CHOICES, default='USD')
    geography = StringField('Geography / Location', validators=[Optional(), Length(max=100)])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=1000)])

    submit = SubmitField('Save Holding')
