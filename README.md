# ⚽ Football Match Analytics MCP

A **Model Context Protocol (MCP) server** that provides statistical analysis
and outcome predictions for football fixtures using multi-source market data,
live team statistics, and a self-learning probability engine.

> Designed for deployment on **Render** and version-controlled on **GitHub**.
> Connects to **Claude Desktop** or any MCP-compatible AI client.

---

## ✨ Features

| Feature | Detail |
|---------|--------|
| 📋 Fixture schedule | Pre-loaded fixture data from two independent providers |
| 📊 Market probability | Removes provider margin to extract true implied probabilities |
| 🤝 Provider consensus | Averages two market sources to reduce noise |
| 📡 Live enrichment | Pulls real-time form & head-to-head data via football-data.org |
| 🧠 Self-learning | Logs predictions; auto-recalibrates weights from recorded outcomes |
| 🌐 Cloud-ready | HTTP transport for Render; stdio for local use |
| 🔁 CI/CD | GitHub Actions: lint → test → auto-deploy on push to `main` |

---

## 🛠️ MCP Tools

| Tool | Description |
|------|-------------|
| `analytics_list_fixtures` | Show today's loaded fixture schedule; filter by country or league |
| `analytics_predict_fixture` | Full statistical analysis for one fixture by provider ID |
| `analytics_bulk_predictions` | Ranked predictions for all fixtures; filterable by confidence |
| `analytics_live_prediction` | Deep prediction using live API data (match ID from football-data.org) |
| `analytics_live_fixtures` | Fetch upcoming fixtures for major leagues from live API |
| `analytics_record_outcome` | Record actual result after a match to train the model |
| `analytics_model_report` | View accuracy statistics and active model weights |

---

## 🚀 Quick Start (Local)

### 1 — Get a free API key

Register at **https://www.football-data.org/client/register** (takes 1 minute).
The free tier covers all top European leagues at 10 requests/minute.

### 2 — Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/football-analytics-mcp.git
cd football-analytics-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3 — Configure

```bash
cp .env.example .env
# Edit .env and paste your FOOTBALL_DATA_API_KEY
```

### 4 — Run

```bash
python server.py
# Output: Football Analytics MCP — stdio transport
```

---

## 🔌 Connect to Claude Desktop

Edit your Claude Desktop config:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "football-analytics": {
      "command": "python",
      "args": ["/full/path/to/football-analytics-mcp/server.py"],
      "env": {
        "FOOTBALL_DATA_API_KEY": "your_key_here"
      }
    }
  }
}
```

Restart Claude Desktop. The analytics tools will appear automatically.

---

## ☁️ Deploy to Render

1. Push this repo to GitHub (see `IMPLEMENTATION_GUIDE.docx` for full steps)
2. Create a Render **Web Service** and connect your GitHub repo
3. Render auto-detects `render.yaml` — click **Apply**
4. Add `FOOTBALL_DATA_API_KEY` in **Render → Environment Variables**
5. Add a **Persistent Disk** (1 GB, mount path: `/opt/render/project/src`)

Once deployed, add the Render URL to Claude Desktop:

```json
{
  "mcpServers": {
    "football-analytics-cloud": {
      "url": "https://football-analytics-mcp.onrender.com/mcp"
    }
  }
}
```

---

## 📊 Prediction Model

The engine blends **five signals** using configurable weights:

| Signal | Default Weight | Description |
|--------|---------------|-------------|
| Market implied probability | 55% | Provider lines → true probability via margin removal |
| Recent form | 20% | Last-5 results: W=3, D=1, L=0 pts (normalised) |
| Head-to-head record | 7% | Historical outcome ratios between the two teams |
| Home field advantage | 8% | Structural home-team boost |
| League position gap | 10% | Current standings differential |

Weights **auto-recalibrate** based on recorded outcomes:
- Accuracy < 60% → market signal weight increases to 65%
- Accuracy > 75% → form signal weight increases to 25%

---

## 💬 Example Prompts

```
"Show me today's fixture schedule for Italy"
"Predict fixture 5348 with full analytics"
"Give me bulk predictions for Brazil, confidence above 65%"
"What does the model report say about its accuracy?"
"Fetch upcoming Serie A fixtures for the next 5 days"
"Record outcome: fixture 5348 was a draw (D)"
```

---

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| `FOOTBALL_DATA_API_KEY not configured` | Add key to `.env` or Render env vars |
| `403 Forbidden` | Free plan may not cover this league — upgrade at football-data.org |
| `429 Rate limit` | Free tier: 10 req/min — wait 60 seconds |
| Fixture ID not found | Run `analytics_list_fixtures` first to see valid IDs |
| Render deploy fails | Check build log — ensure `requirements.txt` is correct |
| Model accuracy low | Record more outcomes — needs 20+ samples to recalibrate |

---

## ⚠️ Disclaimer

This tool provides **statistical analysis only** — predictions are probabilistic,
not guaranteed. Football outcomes are inherently uncertain. Always assess
independently before making any decisions based on model output.
