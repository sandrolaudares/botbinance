---
name: testing-trading-bot
description: Test the Binance trading bot dashboard and strategies end-to-end. Use when verifying UI, strategy activation, or API changes.
---

# Testing the Binance Trading Bot

## Environment
- **Deployed app**: https://binance-trading-bot-sandro.fly.dev/
- **Deploy command**: `fly deploy -a binance-trading-bot-sandro`
- **Lint**: `ruff check .`
- **Tests**: `pytest tests/ -q` (17 tests)
- **Repo**: `/home/ubuntu/repos/binance-trading-bot`

## Devin Secrets Needed
- `BINANCE_API_KEY` (repo-scoped, permanent)
- `BINANCE_API_SECRET` (repo-scoped, permanent)

## How to Test

### Prerequisites
1. After deploy, the app may start in Paper Trading mode. Activate live mode first:
   ```
   curl -s -X POST https://binance-trading-bot-sandro.fly.dev/api/mode/live
   ```
2. Activate strategies via API before testing UI:
   ```
   curl -s -X POST .../api/scalping/activate -H "Content-Type: application/json" -d '{"active":true,"quote_asset":"USDT",...}'
   curl -s -X POST .../api/breakout/activate -H "Content-Type: application/json" -d '{"active":true,"quote_asset":"USDT",...}'
   ```

### Key Things to Verify
1. **Dashboard loads** at the fly.dev URL with all tabs visible (Scalping, Breakout, Smart Trade, Auto-Invest, Mercado, Historico)
2. **Scalping tab**: Shows "ATIVO (USDT)" status, "Pares monitorados: ~440", activity log with SCAN and TREND FILTER entries
3. **Breakout tab**: Shows "ATIVO (USDT)" status, config form with TP1/TP2/SL fields, activity log with SCAN entries
4. **Deactivate/Reactivate cycle**: Click Desativar -> status changes to "desativado" -> Click Ativar -> status returns to "ATIVO"
5. **Activity logs auto-refresh** every 10 seconds

### Common Issues
- **fly.io IP changes on redeploy**: The Binance API key might have IP restrictions. If trades fail with 401, the user needs to update the IP whitelist or set "Unrestricted" on their Binance API key.
- **App state resets on redeploy**: Live mode and strategy activation are in-memory. Must re-activate after each `fly deploy`.
- **Market closed for BRL pairs**: Some BRL pairs close outside Brazilian business hours. USDT pairs trade 24/7.
- **Trend filter blocks all buys**: If BTC and ETH are both trending down, the scalping trend filter will skip all buys. This is expected behavior - check the activity log for "TREND FILTER BTC+ETH Mercado em queda" entries.
- **No breakout trades**: Breakouts require specific market conditions (consolidation + volume spike). Not finding breakouts during a short test window is normal.

### API Endpoints for Verification
```
GET  /api/scalping/status    -> {active, known_pairs, positions, activity_log}
GET  /api/breakout/status    -> {active, positions, activity_log}
GET  /api/mode               -> {is_live, paper_trading}
POST /api/scalping/activate  -> activate with config
POST /api/breakout/activate  -> activate with config
POST /api/scalping/deactivate
POST /api/breakout/deactivate
```
