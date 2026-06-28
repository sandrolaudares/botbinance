# Binance Trading Bot

Bot de trading automatizado com a API da Binance, focado em **retorno a curtíssimo prazo** com gerenciamento de risco.

## Estratégias

### Grid Trading
Compra e venda automática em faixas de preço. Lucra com oscilações dentro da faixa configurada.

### DCA (Dollar Cost Averaging)
Compras periódicas de valor fixo em BRL, reduzindo o impacto da volatilidade.

### Auto-Invest
Análise técnica automática (RSI, MACD, Bollinger Bands) para identificar as melhores oportunidades e alocar investimento proporcionalmente.

## Modo Paper Trading

O bot inicia em **modo simulação** por padrão. Nenhum dinheiro real é usado até que você desative o paper trading.

## Começando

### 1. Instalar dependências

```bash
pip install -e ".[dev]"
```

### 2. Configurar

Copie o arquivo de exemplo e configure suas chaves:

```bash
cp .env.example .env
```

### 3. Executar

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Acesse o dashboard em: http://localhost:8000

### 4. Executar testes

```bash
pytest
```

## API Endpoints

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/portfolio` | Estado atual do portfólio |
| GET | `/api/trades` | Histórico de trades |
| GET | `/api/stats` | Estatísticas de trading |
| POST | `/api/grid/setup` | Configurar grid trading |
| POST | `/api/dca/setup` | Configurar DCA |
| POST | `/api/auto-invest/setup` | Configurar auto-invest |
| POST | `/api/auto-invest/rebalance` | Forçar rebalanceamento |
| POST | `/api/market/scan` | Escanear mercado |
| GET | `/api/market/top` | Top pares BRL por volume |

## Tecnologias

- **FastAPI** - Backend API
- **python-binance** - Integração com Binance
- **pandas/numpy** - Análise de dados
- **ta** - Indicadores técnicos
- **APScheduler** - Agendamento de tarefas

## Aviso

Este bot é para fins educacionais. Criptomoedas são investimentos de alto risco. Sempre use o modo paper trading primeiro para testar estratégias.
