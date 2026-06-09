# US / Canada Stock Screener Bot

Scans US + Canadian stocks weekly, scores them on growth, quality, and
technical trend (CANSLIM / Minervini-style relative strength), and sends a
ranked report to Telegram every Saturday via GitHub Actions.

Screening tool only — not investment advice.

## Settings (in .github/workflows/screener.yml)
- UNIVERSE: SP500 | NASDAQ | ALL_US | ALL_NA | TSX | BUNDLED
- MAX_STOCKS: 0 for all, or a number to cap a scan
- TEST_MODE: "True" for a quick 5-stock check

## Secrets (repo Settings -> Secrets -> Actions)
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
