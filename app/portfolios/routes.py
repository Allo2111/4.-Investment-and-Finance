from flask import Blueprint, render_template, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Portfolio, AssetHolding, LISTED_CLASSES
from .forms import PortfolioForm, AssetHoldingForm

portfolios_bp = Blueprint('portfolios', __name__)


# ── Dashboard ──────────────────────────────────────────────

@portfolios_bp.route('/dashboard')
@login_required
def dashboard():
    portfolios = current_user.portfolios.order_by(Portfolio.updated_at.desc()).all()
    return render_template('portfolios/dashboard.html', portfolios=portfolios)


# ── Portfolio CRUD ─────────────────────────────────────────

@portfolios_bp.route('/portfolios')
@login_required
def list_portfolios():
    portfolios = current_user.portfolios.order_by(Portfolio.updated_at.desc()).all()
    return render_template('portfolios/list.html', portfolios=portfolios)


@portfolios_bp.route('/portfolios/new', methods=['GET', 'POST'])
@login_required
def new_portfolio():
    form = PortfolioForm()
    if form.validate_on_submit():
        p = Portfolio(
            user_id=current_user.id,
            name=form.name.data,
            description=form.description.data,
            base_currency=form.base_currency.data,
        )
        db.session.add(p)
        db.session.commit()
        flash(f'Portfolio "{p.name}" created.', 'success')
        return redirect(url_for('portfolios.detail', portfolio_id=p.id))
    return render_template('portfolios/form.html', form=form, title='New Portfolio', portfolio=None)


@portfolios_bp.route('/portfolios/<int:portfolio_id>')
@login_required
def detail(portfolio_id):
    p        = _owned_portfolio_or_404(portfolio_id)
    holdings = p.asset_holdings.all()
    runs     = p.analysis_runs.limit(10).all()
    totals   = _compute_totals(holdings)
    allocation = _compute_allocation(holdings)
    return render_template('portfolios/detail.html',
                           portfolio=p, holdings=holdings, runs=runs,
                           totals=totals, allocation=allocation)


@portfolios_bp.route('/portfolios/<int:portfolio_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_portfolio(portfolio_id):
    p    = _owned_portfolio_or_404(portfolio_id)
    form = PortfolioForm(obj=p)
    if form.validate_on_submit():
        p.name          = form.name.data
        p.description   = form.description.data
        p.base_currency = form.base_currency.data
        db.session.commit()
        flash('Portfolio updated.', 'success')
        return redirect(url_for('portfolios.detail', portfolio_id=p.id))
    return render_template('portfolios/form.html', form=form, title='Edit Portfolio', portfolio=p)


@portfolios_bp.route('/portfolios/<int:portfolio_id>/delete', methods=['POST'])
@login_required
def delete_portfolio(portfolio_id):
    p = _owned_portfolio_or_404(portfolio_id)
    db.session.delete(p)
    db.session.commit()
    flash(f'Portfolio "{p.name}" deleted.', 'info')
    return redirect(url_for('portfolios.list_portfolios'))


# ── Holdings CRUD ──────────────────────────────────────────

@portfolios_bp.route('/portfolios/<int:portfolio_id>/holdings/new', methods=['GET', 'POST'])
@login_required
def add_holding(portfolio_id):
    p    = _owned_portfolio_or_404(portfolio_id)
    form = AssetHoldingForm()

    if form.validate_on_submit():
        asset_class = form.asset_class.data
        is_listed   = asset_class in LISTED_CLASSES
        symbol      = (form.symbol.data or '').strip().upper() or None
        name        = form.name.data.strip() or symbol or 'Unnamed'

        h = AssetHolding(
            portfolio_id=p.id,
            asset_class=asset_class,
            symbol=symbol,
            name=name,
            is_listed=is_listed,
            quantity=form.quantity.data         if is_listed      else None,
            avg_cost=form.avg_cost.data         if is_listed      else None,
            manual_value=form.manual_value.data if not is_listed  else None,
            currency=form.currency.data or p.base_currency,
            geography=form.geography.data or None,
            notes=form.notes.data or None,
        )
        db.session.add(h)
        db.session.commit()

        # Auto-fetch current price for listed holdings so value is available immediately.
        if is_listed and symbol:
            _fetch_and_store_price(h)
            db.session.commit()

        flash(f'"{name}" added.', 'success')
        return redirect(url_for('portfolios.detail', portfolio_id=p.id))

    return render_template('portfolios/holding_form.html',
                           form=form, portfolio=p, title='Add Holding', holding=None)


@portfolios_bp.route('/holdings/<int:holding_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_holding(holding_id):
    h = _owned_holding_or_404(holding_id)
    p = h.portfolio

    form = AssetHoldingForm(obj=h)
    if form.validate_on_submit():
        asset_class = form.asset_class.data
        is_listed   = asset_class in LISTED_CLASSES
        symbol      = (form.symbol.data or '').strip().upper() or None
        name        = form.name.data.strip() or symbol or 'Unnamed'

        symbol_changed = (symbol != h.symbol)

        h.asset_class  = asset_class
        h.is_listed    = is_listed
        h.symbol       = symbol
        h.name         = name
        h.quantity     = form.quantity.data         if is_listed      else None
        h.avg_cost     = form.avg_cost.data         if is_listed      else None
        h.manual_value = form.manual_value.data     if not is_listed  else None
        h.currency     = form.currency.data or p.base_currency
        h.geography    = form.geography.data or None
        h.notes        = form.notes.data or None

        # Re-fetch price if the symbol changed or no price is stored yet.
        if is_listed and symbol and (symbol_changed or not h.current_price):
            _fetch_and_store_price(h)

        db.session.commit()
        flash(f'"{name}" updated.', 'success')
        return redirect(url_for('portfolios.detail', portfolio_id=p.id))

    return render_template('portfolios/holding_form.html',
                           form=form, portfolio=p, title='Edit Holding', holding=h)


@portfolios_bp.route('/holdings/<int:holding_id>/delete', methods=['POST'])
@login_required
def delete_holding(holding_id):
    h            = _owned_holding_or_404(holding_id)
    portfolio_id = h.portfolio_id
    name         = h.name
    db.session.delete(h)
    db.session.commit()
    flash(f'"{name}" removed.', 'info')
    return redirect(url_for('portfolios.detail', portfolio_id=portfolio_id))


# ── Price refresh (JSON API) ───────────────────────────────

@portfolios_bp.route('/portfolios/<int:portfolio_id>/refresh-prices', methods=['POST'])
@login_required
def refresh_prices(portfolio_id):
    p      = _owned_portfolio_or_404(portfolio_id)
    listed = [h for h in p.asset_holdings.all() if h.is_listed and h.symbol]

    if not listed:
        return jsonify({'updated': 0})

    from ..analysis.services import fetch_current_prices
    symbols = list({h.symbol for h in listed})
    prices  = fetch_current_prices(symbols)

    updated = 0
    for h in listed:
        price = prices.get(h.symbol)
        if price:
            h.current_price = price
            if h.quantity:
                h.market_value = round(h.quantity * price, 2)
            updated += 1

    db.session.commit()

    results = [
        {'id': h.id, 'current_price': h.current_price, 'effective_value': h.effective_value}
        for h in p.asset_holdings.all()
    ]
    return jsonify({'updated': updated, 'holdings': results})


# ── Helpers ────────────────────────────────────────────────

def _fetch_and_store_price(holding: AssetHolding) -> None:
    """Fetch the latest price for a single listed holding and update current_price + market_value.
    Silently skips if yfinance returns nothing (e.g. bad symbol or network issue)."""
    from ..analysis.services import fetch_current_prices
    prices = fetch_current_prices([holding.symbol])
    price  = prices.get(holding.symbol)
    if price:
        holding.current_price = price
        if holding.quantity:
            holding.market_value = round(holding.quantity * price, 2)


def _compute_allocation(holdings):
    """Return a list of {label, cls, value} dicts for the pie chart, sorted by value desc.
    Liabilities are included as a separate negative-value slice."""
    from collections import defaultdict
    buckets: dict[str, dict] = defaultdict(lambda: {'value': 0.0, 'cls': ''})
    liability_total = 0.0
    for h in holdings:
        v = h.effective_value
        if not v:
            continue
        if h.is_liability:
            liability_total += v
        else:
            key = h.asset_class_label
            buckets[key]['value'] += v
            buckets[key]['cls']    = h.asset_class
    result = [
        {'label': k, 'value': round(v['value'], 2), 'cls': v['cls']}
        for k, v in buckets.items()
    ]
    result.sort(key=lambda x: x['value'], reverse=True)
    if liability_total:
        result.append({'label': 'Liabilities', 'value': round(liability_total, 2), 'cls': 'liability'})
    return result


def _compute_totals(holdings):
    gross       = 0.0
    liabilities = 0.0
    for h in holdings:
        v = h.effective_value or 0
        if h.is_liability:
            liabilities += v
        else:
            gross += v
    return {
        'gross':       round(gross, 2),
        'liabilities': round(liabilities, 2),
        'net':         round(gross - liabilities, 2),
        'count':       len(holdings),
    }


def _owned_portfolio_or_404(portfolio_id):
    p = Portfolio.query.filter_by(id=portfolio_id, user_id=current_user.id).first()
    if not p:
        abort(404)
    return p


def _owned_holding_or_404(holding_id):
    h = db.get_or_404(AssetHolding, holding_id)
    if h.portfolio.user_id != current_user.id:
        abort(403)
    return h
