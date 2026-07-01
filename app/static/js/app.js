// Binance Trading Bot - Frontend Application

const API_BASE = '';

// ========== Utility Functions ==========

function formatUSD(value) {
    return `$ ${parseFloat(value).toFixed(2)}`;
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

    document.getElementById('total-balance').textContent = formatUSD(data.total_balance_brl);
    document.getElementById('available-brl').textContent = formatUSD(data.available_brl);
    document.getElementById('positions-value').textContent = formatUSD(data.positions_value_brl);
    
    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = `${formatUSD(data.total_pnl)} (${formatPercent(data.total_pnl_percent)})`;
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
                        Entrada: ${formatUSD(pos.avg_entry_price)} | 
                        Atual: ${formatUSD(pos.current_price)}
                    </span>
                </div>
                <div class="position-pnl ${pnlClass}">
                    ${formatUSD(pos.unrealized_pnl)}<br>
                    <small>${formatPercent(pos.unrealized_pnl_percent)}</small>
                </div>
            </div>
        `;
    }).join('');
}

// ========== Market ==========

async function refreshMarket() {
    const container = document.getElementById('market-pairs');
    container.innerHTML = '<p class="empty-state">Carregando...</p>';
    
    const data = await apiCall('/api/market/top?limit=20');
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="empty-state">Erro ao carregar dados do mercado</p>';
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr><th>Par</th><th>Preço</th><th>24h</th><th>Volume (USDT)</th></tr>
            </thead>
            <tbody>
                ${data.map(t => {
                    const change = parseFloat(t.priceChangePercent || 0);
                    const changeClass = change >= 0 ? 'buy' : 'sell';
                    return `
                        <tr>
                            <td><strong>${t.symbol}</strong></td>
                            <td>${formatUSD(parseFloat(t.lastPrice || 0))}</td>
                            <td class="${changeClass}">${formatPercent(change)}</td>
                            <td>${formatUSD(parseFloat(t.quoteVolume || 0))}</td>
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
            <div class="stat-item">P&L Realizado: <span class="${stats.total_realized_pnl >= 0 ? 'buy' : 'sell'}">${formatUSD(stats.total_realized_pnl)}</span></div>
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
                        <td>${formatUSD(t.price)}</td>
                        <td>${formatUSD(t.total_brl)}</td>
                        <td class="${t.pnl >= 0 ? 'buy' : 'sell'}">${t.pnl !== 0 ? formatUSD(t.pnl) : '-'}</td>
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
                case 'scalping': updateScalpingStatus(); break;
                case 'breakout': updateBreakoutStatus(); break;
                case 'market': refreshMarket(); break;
                case 'trades': updateTrades(); break;
            }
        });
    });
}

// ========== Scalping ==========

async function activateScalping(e) {
    e.preventDefault();
    const config = {
        active: true,
        quote_asset: "USDT",
        amount_per_trade: parseFloat(document.getElementById('scalp-amount').value),
        amount_per_trade_strong: parseFloat(document.getElementById('scalp-amount-strong').value),
        trailing_percent: parseFloat(document.getElementById('scalp-trail').value),
        stop_loss_percent: parseFloat(document.getElementById('scalp-sl').value),
        max_hold_minutes: parseInt(document.getElementById('scalp-minutes').value),
        max_concurrent_trades: parseInt(document.getElementById('scalp-max').value),
        bearish_enabled: true,
    };
    const result = await apiCall('/api/scalping/activate', 'POST', config);
    if (result && result.status === 'activated') {
        document.getElementById('btn-activate-scalp').textContent = 'Reconfigurar Scalping';
        document.getElementById('btn-deactivate-scalp').style.display = 'inline-block';
        updateScalpingStatus();
    }
}

async function deactivateScalping() {
    await apiCall('/api/scalping/deactivate', 'POST');
    document.getElementById('btn-activate-scalp').textContent = 'Ativar Scalping';
    document.getElementById('btn-deactivate-scalp').style.display = 'none';
    document.getElementById('scalping-status').innerHTML = '<p>Scalping desativado</p>';
}

async function updateScalpingStatus() {
    const data = await apiCall('/api/scalping/status');
    if (!data) return;

    const statusEl = document.getElementById('scalping-status');
    const posEl = document.getElementById('scalping-positions');

    if (data.active) {
        document.getElementById('btn-activate-scalp').textContent = 'Reconfigurar Scalping';
        document.getElementById('btn-deactivate-scalp').style.display = 'inline-block';
        const qa = data.quote_asset || 'USDT';
        statusEl.innerHTML = `<p><strong>ATIVO 24/7 (${qa})</strong> | Pares: ${data.known_pairs} | Posições: ${data.active_positions} | Fechadas: ${data.closed_positions}</p>`;
    } else {
        statusEl.innerHTML = '<p>Scalping desativado</p>';
    }

    if (data.positions && data.positions.length > 0) {
        let html = '<table class="data-table"><thead><tr><th>Par</th><th>Entrada</th><th>Tipo</th><th>Status</th><th>Peak</th><th>Ação</th></tr></thead><tbody>';
        for (const pos of data.positions) {
            const trailing = pos.trailing_active ? 'TRAILING' : 'Monitorando';
            const isBear = pos.id.startsWith('bear_');
            const tipo = isBear ? '<span style="color:#ff9f43">BEAR</span>' : '<span style="color:#4ecdc4">BULL</span>';
            html += `<tr>
                <td>${pos.symbol}</td>
                <td>${pos.entry_price.toFixed(6)}</td>
                <td>${tipo}</td>
                <td>${trailing}</td>
                <td>${pos.highest_price.toFixed(6)}</td>
                <td><button class="btn btn-small btn-danger" onclick="closeScalpPosition('${pos.id}')">Fechar</button></td>
            </tr>`;
        }
        html += '</tbody></table>';
        posEl.innerHTML = html;
    } else {
        posEl.innerHTML = data.active ? '<p>Aguardando sinais...</p>' : '';
    }

    if (data.history && data.history.length > 0) {
        let hist = '<h4 style="margin-top:10px">Últimos Fechados</h4><table class="data-table"><thead><tr><th>Par</th><th>PnL</th><th>Motivo</th></tr></thead><tbody>';
        for (const h of data.history) {
            const pnl = h.pnl_percent != null ? `${h.pnl_percent.toFixed(2)}%` : '-';
            const cls = (h.pnl_percent || 0) >= 0 ? 'profit' : 'loss';
            hist += `<tr><td>${h.symbol}</td><td class="${cls}">${pnl}</td><td>${h.reason}</td></tr>`;
        }
        hist += '</tbody></table>';
        posEl.innerHTML += hist;
    }

    // Activity log (real-time) - expanded
    const logEl = document.getElementById('activity-log');
    if (data.activity_log && data.activity_log.length > 0) {
        let logHtml = '';
        for (const entry of data.activity_log.slice().reverse()) {
            const time = new Date(entry.time).toLocaleTimeString('pt-BR');
            let color = '#aaa';
            if (entry.action.includes('COMPRA BEARISH')) color = '#ff9f43';
            else if (entry.action.includes('COMPRA')) color = '#4ecdc4';
            else if (entry.action.includes('VENDA')) color = '#ff6b6b';
            else if (entry.action.includes('BEARISH DETECTADO')) color = '#ff9f43';
            else if (entry.action.includes('MOMENTUM')) color = '#ffd93d';
            else if (entry.action.includes('NOVA')) color = '#6bcb77';
            else if (entry.action.includes('RE-ENTRY')) color = '#a29bfe';
            else if (entry.action.includes('FALHA')) color = '#636e72';
            else if (entry.action === 'SCAN') color = '#555';
            logHtml += `<div style="margin-bottom:4px;color:${color}"><span style="color:#888">[${time}]</span> <strong>${entry.action}</strong> ${entry.symbol} <span style="color:#999">${entry.details}</span></div>`;
        }
        logEl.innerHTML = logHtml;
    } else {
        logEl.innerHTML = '<p style="color:#666">Aguardando atividade...</p>';
    }
}

async function closeScalpPosition(posId) {
    if (!confirm('Fechar esta posição de scalping?')) return;
    await apiCall(`/api/scalping/${posId}/close`, 'POST');
    updateScalpingStatus();
}

// ========== Breakout ==========

async function activateBreakout(e) {
    e.preventDefault();
    const config = {
        active: true,
        quote_asset: "USDT",
        amount_per_trade: parseFloat(document.getElementById('brk-amount').value),
        amount_per_trade_strong: parseFloat(document.getElementById('brk-amount-strong').value),
        max_concurrent_trades: parseInt(document.getElementById('brk-max').value),
        tp1_percent: parseFloat(document.getElementById('brk-tp1').value),
        tp2_percent: parseFloat(document.getElementById('brk-tp2').value),
        stop_loss_percent: parseFloat(document.getElementById('brk-sl').value),
    };
    const result = await apiCall('/api/breakout/activate', 'POST', config);
    if (result && result.status === 'activated') {
        document.getElementById('btn-activate-brk').textContent = 'Reconfigurar Breakout';
        document.getElementById('btn-deactivate-brk').style.display = 'inline-block';
        updateBreakoutStatus();
    } else {
        alert(`Erro: ${result?.error || 'Falha ao ativar Breakout'}`);
    }
}

async function deactivateBreakout() {
    await apiCall('/api/breakout/deactivate', 'POST');
    document.getElementById('btn-activate-brk').textContent = 'Ativar Breakout';
    document.getElementById('btn-deactivate-brk').style.display = 'none';
    document.getElementById('breakout-status').innerHTML = '<p>Breakout desativado</p>';
}

async function updateBreakoutStatus() {
    const data = await apiCall('/api/breakout/status');
    if (!data) return;

    const statusEl = document.getElementById('breakout-status');
    const posEl = document.getElementById('breakout-positions');

    if (data.active) {
        document.getElementById('btn-activate-brk').textContent = 'Reconfigurar Breakout';
        document.getElementById('btn-deactivate-brk').style.display = 'inline-block';
        statusEl.innerHTML = `<p><strong>ATIVO (${data.quote_asset || 'USDT'})</strong> | Posições: ${data.active_positions}</p>`;
    } else {
        statusEl.innerHTML = '<p>Breakout desativado</p>';
    }

    if (data.positions && data.positions.length > 0) {
        let html = '<table class="data-table"><thead><tr><th>Par</th><th>Entrada</th><th>Resistência</th><th>TP1</th><th>Peak</th><th>Restante</th><th>Ação</th></tr></thead><tbody>';
        for (const pos of data.positions) {
            const tp1 = pos.tp1_hit ? 'HIT' : 'Aguardando';
            html += `<tr>
                <td>${pos.symbol}</td>
                <td>${pos.entry_price.toFixed(6)}</td>
                <td>${pos.resistance_level.toFixed(6)}</td>
                <td>${tp1}</td>
                <td>${pos.highest_price.toFixed(6)}</td>
                <td>${pos.remaining_quantity.toFixed(6)}</td>
                <td><button class="btn btn-small btn-danger" onclick="closeBreakoutPosition('${pos.id}')">Fechar</button></td>
            </tr>`;
        }
        html += '</tbody></table>';
        posEl.innerHTML = html;
    } else {
        posEl.innerHTML = data.active ? '<p>Aguardando breakouts...</p>' : '';
    }

    if (data.history && data.history.length > 0) {
        let hist = '<h4 style="margin-top:10px">Histórico</h4><table class="data-table"><thead><tr><th>Par</th><th>PnL</th><th>Motivo</th></tr></thead><tbody>';
        for (const h of data.history) {
            const pnl = h.pnl_percent != null ? `${h.pnl_percent.toFixed(2)}%` : '-';
            const cls = (h.pnl_percent || 0) >= 0 ? 'profit' : 'loss';
            hist += `<tr><td>${h.symbol}</td><td class="${cls}">${pnl}</td><td>${h.reason}</td></tr>`;
        }
        hist += '</tbody></table>';
        posEl.innerHTML += hist;
    }

    const logEl = document.getElementById('breakout-log');
    if (data.activity_log && data.activity_log.length > 0) {
        let logHtml = '';
        for (const entry of data.activity_log.slice().reverse()) {
            const time = new Date(entry.time).toLocaleTimeString('pt-BR');
            let color = '#aaa';
            if (entry.action.includes('COMPRA')) color = '#4ecdc4';
            else if (entry.action.includes('VENDA')) color = '#ff6b6b';
            else if (entry.action.includes('BREAKOUT')) color = '#ffd93d';
            else if (entry.action === 'SCAN') color = '#555';
            logHtml += `<div style="margin-bottom:4px;color:${color}"><span style="color:#888">[${time}]</span> <strong>${entry.action}</strong> ${entry.symbol} <span style="color:#999">${entry.details}</span></div>`;
        }
        logEl.innerHTML = logHtml;
    } else {
        logEl.innerHTML = '<p style="color:#666">Aguardando atividade...</p>';
    }
}

async function closeBreakoutPosition(posId) {
    if (!confirm('Fechar esta posição de breakout?')) return;
    await apiCall(`/api/breakout/${posId}/close`, 'POST');
    updateBreakoutStatus();
}

// ========== Event Listeners ==========

function initEventListeners() {
    document.getElementById('scalping-form').addEventListener('submit', activateScalping);
    document.getElementById('btn-deactivate-scalp').addEventListener('click', deactivateScalping);
    document.getElementById('breakout-form').addEventListener('submit', activateBreakout);
    document.getElementById('btn-deactivate-brk').addEventListener('click', deactivateBreakout);
    document.getElementById('btn-refresh-market').addEventListener('click', refreshMarket);
}

// ========== Auto-refresh ==========

function startAutoRefresh() {
    updatePortfolio();
    setInterval(updatePortfolio, 15000);
    setInterval(updateScalpingStatus, 10000);
    setInterval(updateBreakoutStatus, 10000);
}

// ========== Initialize ==========

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initEventListeners();
    startAutoRefresh();
    updateScalpingStatus();
    updateBreakoutStatus();
});
