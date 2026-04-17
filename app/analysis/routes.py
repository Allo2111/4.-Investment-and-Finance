import json
from datetime import datetime
from flask import Blueprint, render_template, request, Response, stream_with_context, jsonify, abort
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Portfolio, AssetHolding, AnalysisRun, AnalysisResult, LISTED_CLASSES
from .services import fetch_market_data, stream_ai_analysis, fetch_current_prices

analysis_bp = Blueprint('analysis', __name__)


# ── Run analysis ──────────────────────────────────────────

@analysis_bp.route('/portfolios/<int:portfolio_id>/run-analysis', methods=['POST'])
@login_required
def run_analysis(portfolio_id):
    portfolio = Portfolio.query.filter_by(id=portfolio_id, user_id=current_user.id).first()
    if not portfolio:
        abort(404)

    all_holdings = portfolio.asset_holdings.all()
    if not all_holdings:
        return jsonify({'error': 'No holdings saved. Add holdings first.'}), 400

    # ── Step 1: refresh prices for listed holdings ────────
    listed = [h for h in all_holdings if h.is_listed and h.symbol]
    if listed:
        symbols = list({h.symbol for h in listed})
        prices = fetch_current_prices(symbols)
        for h in listed:
            p = prices.get(h.symbol)
            if p:
                h.current_price = p
                if h.quantity:
                    h.market_value = round(h.quantity * p, 2)
        db.session.commit()

    # ── Step 2: split into equity/ETF vs everything else ─
    equity_holdings = [
        h for h in all_holdings
        if h.asset_class in LISTED_CLASSES and h.symbol and h.effective_value
    ]
    other_holdings = [h for h in all_holdings if h not in equity_holdings]

    # ── Step 3: compute weights for equity subset ─────────
    total_equity_value = sum(h.effective_value for h in equity_holdings)
    total_portfolio_value = sum((h.effective_value or 0) for h in all_holdings
                                if not h.is_liability)
    liability_value = sum((h.effective_value or 0) for h in all_holdings if h.is_liability)

    if total_equity_value == 0 or len(equity_holdings) < 2:
        return jsonify({
            'error': 'Need at least 2 listed equity/ETF holdings with known values to run analysis. '
                     'Add holdings with quantity, or ensure prices are available.'
        }), 400

    equity_for_analysis = [
        {
            'ticker': h.symbol,
            'weight': round(h.effective_value / total_equity_value * 100, 2),
        }
        for h in equity_holdings
    ]

    # ── Step 4: build non-equity summary for LLM context ─
    other_by_class: dict[str, float] = {}
    for h in other_holdings:
        if not h.is_liability:
            v = h.effective_value or 0
            label = h.asset_class_label
            other_by_class[label] = other_by_class.get(label, 0) + v

    other_summary = {
        'total_other_value': sum(other_by_class.values()),
        'total_portfolio_value': total_portfolio_value,
        'liability_value': liability_value,
        'by_class': other_by_class,
    } if other_holdings else None

    # ── Step 5: fetch market data ─────────────────────────
    metrics, err = fetch_market_data(equity_for_analysis)
    if err:
        return jsonify({'error': err}), 400

    # ── Step 6: create run record ─────────────────────────
    run = AnalysisRun(
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        status='running',
    )
    db.session.add(run)
    db.session.commit()

    db.session.add(AnalysisResult(
        analysis_run_id=run.id,
        module_name='market_data',
        payload_json={
            **metrics,
            'holdings': equity_for_analysis,
            'other_summary': other_summary,
            'total_portfolio_value': total_portfolio_value,
        },
    ))
    db.session.commit()

    run_id = run.id
    captured_text = []

    def generate():
        yield f"data: {json.dumps({'run_id': run_id, 'metrics': metrics})}\n\n"

        for chunk in stream_ai_analysis(equity_for_analysis, metrics, other_summary):
            try:
                payload = json.loads(chunk.removeprefix('data: ').strip())
                if payload.get('text'):
                    captured_text.append(payload['text'])
            except Exception:
                pass
            yield chunk

        yield 'data: [DONE]\n\n'

        full_text = ''.join(captured_text)
        run_obj = db.session.get(AnalysisRun, run_id)
        if run_obj:
            run_obj.status = 'complete'
            run_obj.summary = full_text[:2000]
            run_obj.completed_at = datetime.utcnow()
            db.session.add(AnalysisResult(
                analysis_run_id=run_id,
                module_name='ai_interpretation',
                payload_json={'text': full_text},
            ))
            db.session.commit()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Analysis history ──────────────────────────────────────

@analysis_bp.route('/portfolios/<int:portfolio_id>/analyses')
@login_required
def history(portfolio_id):
    portfolio = Portfolio.query.filter_by(id=portfolio_id,
                                          user_id=current_user.id).first_or_404()
    runs = portfolio.analysis_runs.limit(50).all()
    return render_template('analysis/history.html', portfolio=portfolio, runs=runs)


# ── Analysis detail ───────────────────────────────────────

@analysis_bp.route('/analyses/<int:run_id>')
@login_required
def detail(run_id):
    run = AnalysisRun.query.filter_by(id=run_id, user_id=current_user.id).first_or_404()
    portfolio = run.portfolio

    market_result = run.results.filter_by(module_name='market_data').first()
    ai_result = run.results.filter_by(module_name='ai_interpretation').first()

    metrics = market_result.payload_json if market_result else None
    ai_text = ai_result.payload_json.get('text', '') if ai_result else run.summary or ''

    return render_template('analysis/detail.html',
                           run=run, portfolio=portfolio,
                           metrics=metrics, ai_text=ai_text)
