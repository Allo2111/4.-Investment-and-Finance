import os
import json
import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, render_template, request, Response, stream_with_context, jsonify
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
_client = None


def get_client():
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not set.")
        _client = OpenAI(api_key=api_key)
    return _client


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data", methods=["POST"])
def get_data():
    data = request.get_json()
    holdings = data.get("holdings", [])
    if not holdings:
        return jsonify({"error": "No holdings provided"}), 400

    tickers = [h["ticker"] for h in holdings]
    download_tickers = list(set(tickers + ["SPY"]))

    try:
        raw = yf.download(download_tickers, period="1y", auto_adjust=True, progress=False)
        prices = raw["Close"] if "Close" in raw.columns else raw

        if isinstance(prices, pd.Series):
            prices = prices.to_frame(name=download_tickers[0])

        prices = prices.dropna(axis=1, how="all")
        valid_tickers = [t for t in tickers if t in prices.columns]

        if len(valid_tickers) < 2:
            return jsonify({"error": "Could not fetch data for enough tickers. Check your ticker symbols."}), 400

        returns = prices.pct_change().dropna()
        port_returns = returns[valid_tickers]

        # Correlation matrix
        corr_matrix = port_returns.corr().round(3)

        # Annualized volatility (%)
        volatility = (port_returns.std() * np.sqrt(252) * 100).round(2).to_dict()

        # Beta vs SPY
        betas = {}
        if "SPY" in returns.columns:
            spy_var = returns["SPY"].var()
            for ticker in valid_tickers:
                cov = returns[ticker].cov(returns["SPY"])
                betas[ticker] = round(cov / spy_var, 2) if spy_var != 0 else None

        # Sector info
        sectors = {}
        for ticker in valid_tickers:
            try:
                info = yf.Ticker(ticker).info
                sectors[ticker] = info.get("sector", "Unknown")
            except Exception:
                sectors[ticker] = "Unknown"

        # 1-year return (%)
        one_year_returns = {}
        for ticker in valid_tickers:
            col = prices[ticker].dropna()
            if len(col) >= 2:
                one_year_returns[ticker] = round((col.iloc[-1] / col.iloc[0] - 1) * 100, 2)

        return jsonify({
            "tickers": valid_tickers,
            "correlation": corr_matrix.to_dict(),
            "volatility": volatility,
            "betas": betas,
            "sectors": sectors,
            "one_year_returns": one_year_returns,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


INTERPRET_PROMPT = """You are a portfolio analyst. A portfolio has been analyzed with REAL market data — actual correlations, volatility, and betas. Your job is to interpret the numbers concisely and give actionable insights.

Reference the actual numbers in your response. Be direct and analytical.

OUTPUT FORMAT (use these exact headers):

### 🧾 Portfolio Diagnosis
[2-3 sentences on the overall structure based on the data]

### ⚠️ Key Risks
[Specific risks — reference actual high correlations, high betas, or concentration]

### 🧩 Missing Exposures
[What's structurally missing based on sectors and correlations]

### 💡 Recommended Stock Types
[2-3 TYPE recommendations linked to detected gaps — NOT specific stocks]

Be concise. Max 350 words total."""


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    holdings = data.get("holdings", [])
    metrics = data.get("metrics", {})

    if not holdings:
        return {"error": "No holdings provided"}, 400

    tickers = metrics.get("tickers", [h["ticker"] for h in holdings])
    weights = {h["ticker"]: h["weight"] for h in holdings}

    # Build structured summary for LLM
    lines = ["Portfolio (real market data):\n"]
    for ticker in tickers:
        parts = [f"{ticker} ({weights.get(ticker, '?')}%)"]
        if ticker in metrics.get("sectors", {}):
            parts.append(f"Sector: {metrics['sectors'][ticker]}")
        if ticker in metrics.get("volatility", {}):
            parts.append(f"Vol: {metrics['volatility'][ticker]}%/yr")
        if ticker in metrics.get("betas", {}):
            parts.append(f"Beta: {metrics['betas'][ticker]}")
        if ticker in metrics.get("one_year_returns", {}):
            parts.append(f"1Y Return: {metrics['one_year_returns'][ticker]}%")
        lines.append("  " + " | ".join(parts))

    # Highlight high correlation pairs
    corr = metrics.get("correlation", {})
    high_pairs = []
    for i, t1 in enumerate(tickers):
        for t2 in tickers[i + 1:]:
            val = corr.get(t1, {}).get(t2)
            if val is not None and float(val) >= 0.7:
                high_pairs.append(f"{t1}/{t2}: {val}")

    if high_pairs:
        lines.append(f"\nHighly correlated pairs (≥0.7): {', '.join(high_pairs)}")
    else:
        lines.append("\nNo pairs with correlation ≥ 0.7 detected.")

    portfolio_text = "\n".join(lines)

    def generate():
        try:
            stream = get_client().chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": INTERPRET_PROMPT},
                    {"role": "user", "content": portfolio_text},
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
        finally:
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
