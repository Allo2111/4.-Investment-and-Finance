import os
import json
import numpy as np
import pandas as pd
import yfinance as yf
from openai import OpenAI

_client = None


def get_openai_client():
    global _client
    if _client is None:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise ValueError('OPENAI_API_KEY is not set.')
        _client = OpenAI(api_key=api_key)
    return _client


# ── Price utilities ───────────────────────────────────────

def fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    """Return {symbol: latest_close_price} for each valid symbol."""
    if not symbols:
        return {}
    try:
        raw = yf.download(symbols, period='5d', auto_adjust=True, progress=False)
        closes = raw['Close'] if 'Close' in raw.columns else raw
        if isinstance(closes, pd.Series):
            closes = closes.to_frame(name=symbols[0])
        closes = closes.dropna(how='all')
        result = {}
        for sym in symbols:
            if sym in closes.columns:
                last = closes[sym].dropna()
                if len(last):
                    result[sym] = round(float(last.iloc[-1]), 4)
        return result
    except Exception:
        return {}


# ── Market data (unchanged core logic) ───────────────────

def fetch_market_data(holdings: list[dict]) -> tuple[dict | None, str | None]:
    """
    Fetch 1-year market data for listed equity/ETF holdings.
    holdings: list of {'ticker': str, 'weight': float}
    Returns (metrics_dict, error_string).
    """
    tickers = [h['ticker'] for h in holdings]
    download_tickers = list(set(tickers + ['SPY']))

    try:
        raw = yf.download(download_tickers, period='1y', auto_adjust=True, progress=False)
        prices = raw['Close'] if 'Close' in raw.columns else raw

        if isinstance(prices, pd.Series):
            prices = prices.to_frame(name=download_tickers[0])

        prices = prices.dropna(axis=1, how='all')
        valid_tickers = [t for t in tickers if t in prices.columns]

        if len(valid_tickers) < 2:
            return None, 'Could not fetch price data for enough tickers. Check your symbols.'

        returns = prices.pct_change().dropna()
        port_returns = returns[valid_tickers]

        corr_matrix = port_returns.corr().round(3)
        volatility = (port_returns.std() * np.sqrt(252) * 100).round(2).to_dict()

        betas = {}
        if 'SPY' in returns.columns:
            spy_var = returns['SPY'].var()
            for ticker in valid_tickers:
                cov = returns[ticker].cov(returns['SPY'])
                betas[ticker] = round(cov / spy_var, 2) if spy_var != 0 else None

        sectors = {}
        for ticker in valid_tickers:
            try:
                info = yf.Ticker(ticker).info
                sectors[ticker] = info.get('sector', 'Unknown')
            except Exception:
                sectors[ticker] = 'Unknown'

        one_year_returns = {}
        for ticker in valid_tickers:
            col = prices[ticker].dropna()
            if len(col) >= 2:
                one_year_returns[ticker] = round((col.iloc[-1] / col.iloc[0] - 1) * 100, 2)

        return {
            'tickers': valid_tickers,
            'correlation': corr_matrix.to_dict(),
            'volatility': volatility,
            'betas': betas,
            'sectors': sectors,
            'one_year_returns': one_year_returns,
        }, None

    except Exception as e:
        return None, str(e)


# ── AI interpretation ─────────────────────────────────────

INTERPRET_PROMPT = """You are a portfolio analyst. You have received real market data for the listed portion of a portfolio, plus a summary of non-listed assets (cash, real estate, etc.).

Your job: interpret the numbers concisely and give actionable insights for the WHOLE portfolio.

Be direct. Reference actual numbers. Max 400 words.

OUTPUT FORMAT (use these exact headers):

### Portfolio Diagnosis
[2-3 sentences on overall structure including non-listed assets if significant]

### Key Risks
[Specific risks — reference actual correlations, betas, concentration, or macro exposure]

### Missing Exposures
[What's structurally absent across the full wealth picture]

### Recommended Actions
[2-3 concrete TYPE-level suggestions, not specific stock picks]"""


def build_portfolio_text(equity_holdings: list[dict], metrics: dict,
                         other_summary: dict | None = None) -> str:
    """Build the LLM prompt text from market metrics + optional non-equity summary."""
    tickers = metrics.get('tickers', [h['ticker'] for h in equity_holdings])
    weights = {h['ticker']: h.get('weight') for h in equity_holdings}

    lines = ['=== Listed Equity / ETF Holdings (real market data) ===\n']
    for ticker in tickers:
        parts = [f'{ticker} ({weights.get(ticker, "?")}%)']
        if ticker in metrics.get('sectors', {}):
            parts.append(f"Sector: {metrics['sectors'][ticker]}")
        if ticker in metrics.get('volatility', {}):
            parts.append(f"Vol: {metrics['volatility'][ticker]}%/yr")
        if ticker in metrics.get('betas', {}):
            parts.append(f"Beta: {metrics['betas'][ticker]}")
        if ticker in metrics.get('one_year_returns', {}):
            parts.append(f"1Y Return: {metrics['one_year_returns'][ticker]}%")
        lines.append('  ' + ' | '.join(parts))

    corr = metrics.get('correlation', {})
    high_pairs = []
    for i, t1 in enumerate(tickers):
        for t2 in tickers[i + 1:]:
            val = corr.get(t1, {}).get(t2)
            if val is not None and float(val) >= 0.7:
                high_pairs.append(f'{t1}/{t2}: {val}')

    if high_pairs:
        lines.append(f'\nHighly correlated pairs (>=0.7): {", ".join(high_pairs)}')
    else:
        lines.append('\nNo pairs with correlation >= 0.7 detected.')

    if other_summary:
        lines.append('\n=== Other Portfolio Assets (manual values) ===')
        total_other = other_summary.get('total_other_value', 0)
        total_portfolio = other_summary.get('total_portfolio_value', 0)
        other_pct = round(total_other / total_portfolio * 100, 1) if total_portfolio else 0
        lines.append(f'Non-listed assets represent {other_pct}% of total portfolio value.')
        for cls, val in other_summary.get('by_class', {}).items():
            pct = round(val / total_portfolio * 100, 1) if total_portfolio else 0
            lines.append(f'  {cls}: {pct}% of portfolio')
        if other_summary.get('liability_value', 0):
            lines.append(f'  Liabilities: {other_summary["liability_value"]:,.0f}')

    return '\n'.join(lines)


def stream_ai_analysis(equity_holdings: list[dict], metrics: dict,
                       other_summary: dict | None = None):
    """Generator yielding SSE-formatted strings."""
    portfolio_text = build_portfolio_text(equity_holdings, metrics, other_summary)
    try:
        stream = get_openai_client().chat.completions.create(
            model='gpt-4o',
            messages=[
                {'role': 'system', 'content': INTERPRET_PROMPT},
                {'role': 'user', 'content': portfolio_text},
            ],
            stream=True,
            temperature=0.2,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield f"data: {json.dumps({'text': delta})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
