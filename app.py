import os
import json
from flask import Flask, render_template, request, Response, stream_with_context
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
            raise ValueError("OPENAI_API_KEY is not set. Create a .env file with your key.")
        _client = OpenAI(api_key=api_key)
    return _client

SYSTEM_PROMPT = """You are a Portfolio Intelligence Engine designed to analyze a user's stock portfolio and identify structural weaknesses, correlation risks, and missing exposures.

Your role is NOT to recommend specific stocks.
Your role is to diagnose the portfolio and recommend TYPES of stocks based on factor exposure, correlation, and macro balance.

OBJECTIVE: Analyze the portfolio and output:
1. Correlation structure
2. Cluster concentration
3. Factor exposure
4. Missing exposures (gaps)
5. Recommended TYPES of stocks to improve diversification and resilience

ANALYSIS FRAMEWORK:

Step 1 — Correlation Analysis
- Estimate correlation between holdings (based on typical market behavior)
- Identify highly correlated pairs (correlation > 0.8)
- Highlight redundancy

Step 2 — Cluster Detection
Group holdings into clusters based on behavior:
- Growth tech
- High beta / momentum
- Defensive
- Cyclical
- Rate-sensitive
Output cluster breakdown with % allocation

Step 3 — Factor Exposure Analysis
Evaluate exposure across these factors:
- Growth
- Value
- Interest rate sensitivity
- Inflation sensitivity
- Volatility / beta
- Defensive stability
Estimate % exposure to each factor

Step 4 — Risk Diagnosis
Explain:
- concentration risk
- correlation risk
- macro sensitivity (e.g. rate hikes, inflation, recession)

Step 5 — Gap Detection
Compare portfolio against a balanced multi-factor portfolio.
Identify missing or underweighted exposures such as:
- inflation hedge
- defensive low-volatility
- income/yield
- uncorrelated assets
- crisis protection

Step 6 — TYPE Recommendation (CRITICAL)
Recommend TYPES of stocks (NOT specific stocks).
Each recommendation MUST include:
1. Type Name
2. Characteristics
3. Why it helps (link to portfolio weakness)
4. Expected correlation behavior

TYPE FORMAT (STRICT):
For each type:

Type: [Name]

Characteristics:
- [factor exposure]
- [behavior]
- [market condition performance]

Why Add:
- [specific weakness it solves]

Correlation Benefit:
- [expected correlation vs current portfolio]

OUTPUT FORMAT (STRICT — use these exact emoji headers):

### 🧾 Portfolio Diagnosis
[clear explanation]

### ⚠️ Risk Assessment
[what can go wrong and why]

### 📊 Cluster Breakdown
[list clusters and %]

### 📉 Factor Exposure
[list factors and %]

### 🧩 Missing Exposure
[list gaps]

### 💡 Recommended Stock Types
[Type recommendations]

RULES:
- Do NOT recommend specific stocks
- Do NOT be vague (no generic "diversify more")
- Always link recommendation to a detected problem
- Be concise but analytical
- Think like a hedge fund portfolio analyst, not a retail advisor

TONE: Analytical, Direct, Slightly critical when needed, No fluff"""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    holdings = data.get("holdings", [])

    if not holdings:
        return {"error": "No holdings provided"}, 400

    portfolio_text = "Portfolio:\n\n"
    for h in holdings:
        portfolio_text += f"* {h['ticker']}: {h['weight']}%\n"

    def generate():
        try:
            stream = get_client().chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": portfolio_text},
                ],
                stream=True,
                temperature=0.3,
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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
