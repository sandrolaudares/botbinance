// Binance Trading Bot - Frontend Application

const API_BASE = '';

// ========== Utility Functions ==========

function formatBRL(value) {
    return new Intl.NumberFormat('pt-BR', {
        style: 'currency',
        currency: 'BRL'
    }).format(value);
}

function formatPercent(value) {
    const sign = value >= 0 ? '+' : '';
    return `${sign}${value.toFixed(2)}%`;
}

function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleString('pt-BR', { 
        day: '2-digit', month: '2-digit', 
        hour: '2-digit', minute: '2-digit' 
    });
}

async function apiCall(endpoint, method = 'GET', body = null) {
    const options = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) options.body = JSON.stringify(body);
    
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, options);
        return await response.json();
    } catch (error) {
        console.error(`API Error (${endpoint}):`, error);
        return null;
    }
}

// ========== Portfolio ==========

async function updatePortfolio() {
    const data = await apiCall('/api/portfolio');
    if (!data) return;

    document.getElementById('total-balance').textContent = formatBRL(data.total_balance_brl);
    document.getElementById('available-brl').textContent = formatBRL(data.available_brl);
    document.getElementById('positions-value').textContent = formatBRL(data.positions_value_brl);
    
    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = `${formatBRL(data.total_pnl)} (${formatPercent(data.total_pnl_percent)})`;
    pnlEl.className = `stat-value ${data.total_pnl >= 0 ? 'positive' : 'negative'}`;

    // Update positions
    updatePositions(data.positions);
}

function updatePositions(positions) {
    const container = document.getElementById('positions-list');
    if (!positions || positions.length === 0) {
        container.innerHTML = '<p class="empty-state">Nenhuma posição aberta</p>';
        return;
    }

    container.innerHTML = positions.map(pos => {
        const pnlClass = pos.unrealized_pnl >= 0 ? 'positive' : 'negative';
        return `
            <div class="position-card">
                <div class="position-info">
                    <span class="position-symbol">${pos.symbol}</span>
                    <span class="position-details">
                        Qtd: ${pos.quantity.toFixed(6)} | 
                        Entrada: ${formatBRL(pos.avg_entry_price)} | 
                        Atual: ${formatBRL(pos.current_price)}
                    </span>
                </div>
                <div class="position-pnl ${pnlClass}">
                    ${formatBRL(pos.unrealized_pnl)}<br>
                    <small>${formatPercent(pos.unrealized_pnl_percent)}</small>
                </div>
            </div>
        `;
    }).join('');
}

// ========== Grid Trading ==========

async function setupGrid(e) {
    e.preventDefault();
    const data = {
        symbol: document.getElementById('grid-symbol').value.toUpperCase(),
        upper_price: parseFloat(document.getElementById('grid-upper').value) || 0,
        lower_price: parseFloat(document.getElementById('grid-lower').value) || 0,
        levels: parseInt(document.getElementById('grid-levels').value),
        investment_brl: parseFloat(document.getElementById('grid-investment').value),
    };
    
    const result = await apiCall('/api/grid/setup', 'POST', data);
    if (result) {
        alert(`Grid configurado: ${result.message}`);
        updateGridStatus();
    }
}

async function updateGridStatus() {
    const data = await apiCall('/api/grid/status');
    const container = document.getElementById('grid-status');
    
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="empty-state">Nenhum grid ativo</p>';
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr><th>Par</th><th>Faixa</th><th>Níveis</th><th>Preenchidos</th><th>Investimento</th><th>Ação</th></tr>
            </thead>
            <tbody>
                ${data.map(g => `
                    <tr>
                        <td><strong>${g.symbol}</strong></td>
                        <td>${formatBRL(g.lower_price)} - ${formatBRL(g.upper_price)}</td>
                        <td>${g.total_levels}</td>
                        <td>${g.filled_levels}/${g.total_levels}</td>
                        <td>${formatBRL(g.investment_brl)}</td>
                        <td><button class="btn btn-danger" onclick="removeGrid('${g.symbol}')">Remover</button></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function removeGrid(symbol) {
    await apiCall(`/api/grid/${symbol}`, 'DELETE');
    updateGridStatus();
}

// ========== DCA ==========

async function setupDCA(e) {
    e.preventDefault();
    const data = {
        symbol: document.getElementById('dca-symbol').value.toUpperCase(),
        amount_brl: parseFloat(document.getElementById('dca-amount').value),
        interval_minutes: parseInt(document.getElementById('dca-interval').value),
    };
    
    const result = await apiCall('/api/dca/setup', 'POST', data);
    if (result) {
        alert(`DCA configurado: ${result.message}`);
        updateDCAStatus();
    }
}

async function updateDCAStatus() {
    const data = await apiCall('/api/dca/status');
    const container = document.getElementById('dca-status');
    
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="empty-state">Nenhum plano DCA ativo</p>';
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr><th>Par</th><th>Valor</th><th>Intervalo</th><th>Execuções</th><th>Total Investido</th><th>Ação</th></tr>
            </thead>
            <tbody>
                ${data.map(d => `
                    <tr>
                        <td><strong>${d.symbol}</strong></td>
                        <td>${formatBRL(d.amount_brl)}</td>
                        <td>${d.interval_minutes} min</td>
                        <td>${d.total_executions}</td>
                        <td>${formatBRL(d.total_invested_brl)}</td>
                        <td><button class="btn btn-danger" onclick="removeDCA('${d.symbol}')">Remover</button></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function removeDCA(symbol) {
    await apiCall(`/api/dca/${symbol}`, 'DELETE');
    updateDCAStatus();
}

// ========== Auto-Invest ==========

async function setupAutoInvest(e) {
    e.preventDefault();
    const data = {
        top_n_coins: parseInt(document.getElementById('ai-topn').value),
        total_investment_brl: parseFloat(document.getElementById('ai-investment').value),
        rebalance_hours: parseInt(document.getElementById('ai-rebalance').value),
    };
    
    const result = await apiCall('/api/auto-invest/setup', 'POST', data);
    if (result) {
        alert(`Auto-Invest: ${result.message}`);
        updateAutoInvestStatus();
    }
}

async function triggerRebalance() {
    const result = await apiCall('/api/auto-invest/rebalance', 'POST');
    if (result) {
        alert(`Rebalanceamento: ${result.trades_executed} trades executados`);
        updatePortfolio();
        updateAutoInvestStatus();
    }
}

async function scanMarket() {
    const container = document.getElementById('market-analysis');
    container.innerHTML = '<p class="empty-state">Analisando mercado...</p>';
    
    const data = await apiCall('/api/market/scan', 'POST');
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="empty-state">Nenhum dado disponível</p>';
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr><th>Par</th><th>Preço</th><th>24h</th><th>RSI</th><th>MACD</th><th>Score</th><th>Recomendação</th></tr>
            </thead>
            <tbody>
                ${data.map(a => {
                    const scoreClass = a.score >= 60 ? 'score-high' : a.score >= 40 ? 'score-mid' : 'score-low';
                    const changeClass = a.change_24h >= 0 ? 'buy' : 'sell';
                    return `
                        <tr>
                            <td><strong>${a.symbol}</strong></td>
                            <td>${formatBRL(a.price)}</td>
                            <td class="${changeClass}">${formatPercent(a.change_24h)}</td>
                            <td>${a.rsi ? a.rsi.toFixed(1) : '-'}</td>
                            <td>${a.macd_signal || '-'}</td>
                            <td><span class="score-badge ${scoreClass}">${a.score.toFixed(0)}</span></td>
                            <td>${a.recommendation}</td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
    `;
}

async function updateAutoInvestStatus() {
    const data = await apiCall('/api/auto-invest/status');
    const container = document.getElementById('auto-invest-status');
    
    if (!data || !data.active) {
        container.innerHTML = '<p class="empty-state">Auto-Invest não configurado</p>';
        return;
    }

    const allocations = Object.entries(data.current_allocations || {});
    container.innerHTML = `
        <div class="stats-row">
            <div class="stat-item">Status: <span>${data.active ? 'Ativo' : 'Inativo'}</span></div>
            <div class="stat-item">Top N: <span>${data.top_n_coins}</span></div>
            <div class="stat-item">Investimento: <span>${formatBRL(data.total_investment_brl)}</span></div>
            <div class="stat-item">Rebalancear: <span>a cada ${data.rebalance_hours}h</span></div>
            <div class="stat-item">Último: <span>${data.last_rebalance ? formatDate(data.last_rebalance) : 'Nunca'}</span></div>
            <div class="stat-item">Trades: <span>${data.total_trades}</span></div>
        </div>
        ${allocations.length > 0 ? `
            <h4 style="margin-top:12px;margin-bottom:8px;">Alocações Atuais</h4>
            <table>
                <thead><tr><th>Par</th><th>Alocação</th></tr></thead>
                <tbody>
                    ${allocations.map(([symbol, pct]) => `
                        <tr><td>${symbol}</td><td>${pct.toFixed(1)}%</td></tr>
                    `).join('')}
                </tbody>
            </table>
        ` : ''}
    `;
}

// ========== Market ==========

async function refreshMarket() {
    const container = document.getElementById('market-pairs');
    container.innerHTML = '<p class="empty-state">Carregando...</p>';
    
    const data = await apiCall('/api/market/top?limit=15');
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="empty-state">Erro ao carregar dados do mercado</p>';
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr><th>Par</th><th>Preço</th><th>24h</th><th>Volume (BRL)</th></tr>
            </thead>
            <tbody>
                ${data.map(t => {
                    const change = parseFloat(t.priceChangePercent || 0);
                    const changeClass = change >= 0 ? 'buy' : 'sell';
                    return `
                        <tr>
                            <td><strong>${t.symbol}</strong></td>
                            <td>${formatBRL(parseFloat(t.lastPrice || 0))}</td>
                            <td class="${changeClass}">${formatPercent(change)}</td>
                            <td>${formatBRL(parseFloat(t.quoteVolume || 0))}</td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
    `;
}

// ========== Trades History ==========

async function updateTrades() {
    const trades = await apiCall('/api/trades?limit=30');
    const stats = await apiCall('/api/stats');
    
    const statsContainer = document.getElementById('trade-stats');
    if (stats) {
        statsContainer.innerHTML = `
            <div class="stat-item">Total: <span>${stats.total_trades}</span></div>
            <div class="stat-item">Ganhos: <span class="buy">${stats.winning_trades}</span></div>
            <div class="stat-item">Perdas: <span class="sell">${stats.losing_trades}</span></div>
            <div class="stat-item">Win Rate: <span>${stats.win_rate.toFixed(1)}%</span></div>
            <div class="stat-item">P&L Realizado: <span class="${stats.total_realized_pnl >= 0 ? 'buy' : 'sell'}">${formatBRL(stats.total_realized_pnl)}</span></div>
        `;
    }

    const container = document.getElementById('trades-list');
    if (!trades || trades.length === 0) {
        container.innerHTML = '<p class="empty-state">Nenhum trade realizado</p>';
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr><th>Data</th><th>Par</th><th>Lado</th><th>Qtd</th><th>Preço</th><th>Total</th><th>P&L</th><th>Estratégia</th></tr>
            </thead>
            <tbody>
                ${trades.map(t => `
                    <tr>
                        <td>${formatDate(t.timestamp)}</td>
                        <td>${t.symbol}</td>
                        <td class="${t.side === 'BUY' ? 'buy' : 'sell'}">${t.side === 'BUY' ? 'Compra' : 'Venda'}</td>
                        <td>${t.quantity.toFixed(6)}</td>
                        <td>${formatBRL(t.price)}</td>
                        <td>${formatBRL(t.total_brl)}</td>
                        <td class="${t.pnl >= 0 ? 'buy' : 'sell'}">${t.pnl !== 0 ? formatBRL(t.pnl) : '-'}</td>
                        <td>${t.strategy}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// ========== Tab Navigation ==========

function initTabs() {
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            tab.classList.add('active');
            document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');
            
            // Refresh relevant data
            switch(tab.dataset.tab) {
                case 'grid': updateGridStatus(); break;
                case 'dca': updateDCAStatus(); break;
                case 'auto-invest': updateAutoInvestStatus(); break;
                case 'market': refreshMarket(); break;
                case 'trades': updateTrades(); break;
            }
        });
    });
}

// ========== Smart Trade (3Commas) ==========

async function createSmartTrade(e) {
    e.preventDefault();
    const config = {
        symbol: document.getElementById('st-symbol').value.toUpperCase(),
        amount_brl: parseFloat(document.getElementById('st-amount').value),
        take_profit_percent: parseFloat(document.getElementById('st-tp').value),
        trailing_percent: parseFloat(document.getElementById('st-trail').value),
        stop_loss_percent: parseFloat(document.getElementById('st-sl').value),
        trailing_stop_loss: true,
    };
    const result = await apiCall('/api/smart-trade/create', 'POST', config);
    if (result && result.status === 'created') {
        alert(`Smart Trade criado! ID: ${result.trade_id}`);
        updateSmartTradeStatus();
    } else {
        alert(`Erro: ${result?.error || 'Falha ao criar Smart Trade'}`);
    }
}

async function autoSmartTrades() {
    const amount = parseFloat(document.getElementById('st-amount').value) || 300;
    const tp = parseFloat(document.getElementById('st-tp').value) || 3.0;
    const trail = parseFloat(document.getElementById('st-trail').value) || 1.0;
    const sl = parseFloat(document.getElementById('st-sl').value) || 5.0;
    
    const result = await apiCall(
        `/api/smart-trade/auto?total_amount_brl=${amount}&top_n=3&take_profit_percent=${tp}&trailing_percent=${trail}&stop_loss_percent=${sl}`,
        'POST'
    );
    if (result && result.trades_created > 0) {
        alert(`${result.trades_created} Smart Trades criados automaticamente!`);
        updateSmartTradeStatus();
    } else {
        alert(`Erro: ${result?.error || 'Nenhum trade criado (sem sinais de compra)'}`);
    }
}

async function updateSmartTradeStatus() {
    const data = await apiCall('/api/smart-trade/active');
    const container = document.getElementById('smart-trade-status');
    const closeAllBtn = document.getElementById('btn-close-all-st');
    
    if (!data || !data.trades || data.trades.length === 0) {
        container.innerHTML = '<p class="empty-state">Nenhum Smart Trade ativo</p>';
        closeAllBtn.style.display = 'none';
        return;
    }
    
    closeAllBtn.style.display = 'block';
    let html = '<table class="data-table"><thead><tr><th>Par</th><th>Status</th><th>TP</th><th>SL</th><th>Lucro</th><th>Ação</th></tr></thead><tbody>';
    
    for (const trade of data.trades) {
        const symbol = trade.symbol || '-';
        const status = trade.status || '-';
        const trailing = trade.trailing_active ? 'TRAILING' : 'Monitorando';
        const pnl = trade.pnl_percent != null ? `${trade.pnl_percent.toFixed(2)}%` : '-';
        const peak = trade.highest_price ? `${trade.highest_price.toFixed(2)}` : '-';
        html += `<tr>
            <td>${symbol}</td>
            <td>${status} ${trade.trailing_active ? '🎯' : ''}</td>
            <td>+${trade.take_profit_percent}% (${trailing})</td>
            <td>-${trade.stop_loss_percent}%</td>
            <td>Peak: R$${peak}</td>
            <td><button class="btn btn-small btn-danger" onclick="closeSmartTrade('${trade.id}')">Fechar</button></td>
        </tr>`;
    }
    html += '</tbody></table>';
    container.innerHTML = html;
}

async function closeSmartTrade(tradeId) {
    if (!confirm('Fechar este Smart Trade a preço de mercado?')) return;
    const result = await apiCall(`/api/smart-trade/${tradeId}/close`, 'POST');
    if (result && result.status === 'closed') {
        updateSmartTradeStatus();
    } else {
        alert('Erro ao fechar trade');
    }
}

async function closeAllSmartTrades() {
    if (!confirm('Fechar TODOS os Smart Trades ativos?')) return;
    const result = await apiCall('/api/smart-trade/close-all', 'POST');
    alert(`${result?.trades_closed || 0} trades fechados`);
    updateSmartTradeStatus();
}

// ========== Event Listeners ==========

function initEventListeners() {
    document.getElementById('smart-trade-form').addEventListener('submit', createSmartTrade);
    document.getElementById('btn-auto-smart').addEventListener('click', autoSmartTrades);
    document.getElementById('btn-close-all-st').addEventListener('click', closeAllSmartTrades);
    document.getElementById('grid-form').addEventListener('submit', setupGrid);
    document.getElementById('dca-form').addEventListener('submit', setupDCA);
    document.getElementById('auto-invest-form').addEventListener('submit', setupAutoInvest);
    document.getElementById('btn-rebalance').addEventListener('click', triggerRebalance);
    document.getElementById('btn-scan').addEventListener('click', scanMarket);
    document.getElementById('btn-refresh-market').addEventListener('click', refreshMarket);
    document.getElementById('btn-reset').addEventListener('click', async () => {
        if (confirm('Tem certeza que deseja resetar a conta? Todos os dados serão perdidos.')) {
            await apiCall('/api/reset', 'POST');
            updatePortfolio();
            updateTrades();
        }
    });
}

// ========== Trading Mode ==========

async function updateTradingMode() {
    const data = await apiCall('/api/mode');
    if (!data) return;
    
    const badge = document.getElementById('trading-mode-badge');
    if (badge) {
        if (data.is_live) {
            badge.textContent = 'LIVE TRADING';
            badge.className = 'badge live';
        } else {
            badge.textContent = 'PAPER TRADING';
            badge.className = 'badge paper';
        }
    }
}

async function toggleTradingMode() {
    const data = await apiCall('/api/mode');
    if (!data) return;
    
    if (data.is_live) {
        if (confirm('Deseja voltar para o modo PAPER TRADING (simulação)?')) {
            await apiCall('/api/mode/paper', 'POST');
        }
    } else {
        if (!data.has_credentials) {
            alert('API Key da Binance não configurada. Configure as variáveis BINANCE_API_KEY e BINANCE_API_SECRET.');
            return;
        }
        if (confirm('ATENÇÃO: Isso vai ativar trading com DINHEIRO REAL na sua conta Binance. Continuar?')) {
            await apiCall('/api/mode/live', 'POST');
        }
    }
    updateTradingMode();
    updatePortfolio();
}

// ========== Auto-refresh ==========

function startAutoRefresh() {
    updatePortfolio();
    updateTradingMode();
    setInterval(updatePortfolio, 15000); // Every 15s
    setInterval(updateTradingMode, 30000); // Every 30s
}

// ========== Initialize ==========

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initEventListeners();
    startAutoRefresh();
    updateSmartTradeStatus();
});
