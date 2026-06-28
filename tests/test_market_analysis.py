import pytest

from app.services.market_analysis import MarketAnalyzer


@pytest.fixture
def analyzer():
    return MarketAnalyzer()


def test_rsi_calculation(analyzer):
    # Generate prices that should give us a known RSI
    prices = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03]
    rsi = analyzer.calculate_rsi(prices)
    assert rsi is not None
    assert 0 <= rsi <= 100


def test_rsi_insufficient_data(analyzer):
    prices = [1, 2, 3]
    rsi = analyzer.calculate_rsi(prices)
    assert rsi is None


def test_macd_calculation(analyzer):
    # Generate enough data points
    prices = list(range(50, 100))
    macd = analyzer.calculate_macd(prices)
    assert macd is not None
    assert "macd" in macd
    assert "signal" in macd
    assert "histogram" in macd


def test_macd_insufficient_data(analyzer):
    prices = [1, 2, 3, 4, 5]
    macd = analyzer.calculate_macd(prices)
    assert macd is None


def test_bollinger_bands(analyzer):
    prices = list(range(1, 30))
    bb = analyzer.calculate_bollinger_bands(prices)
    assert bb is not None
    assert bb["lower"] < bb["middle"] < bb["upper"]
    assert bb["current"] == prices[-1]


def test_bollinger_insufficient_data(analyzer):
    prices = [1, 2, 3]
    bb = analyzer.calculate_bollinger_bands(prices)
    assert bb is None


def test_ema_calculation(analyzer):
    prices = list(range(1, 30))
    ema = analyzer.calculate_ema(prices, period=10)
    assert ema is not None
    assert ema > 0


def test_score_calculation(analyzer):
    # Oversold RSI should give high score
    score = analyzer._calculate_score(
        rsi=25, macd_data={"histogram": 0.5, "macd": 1, "signal": 0.5},
        bb=None, change_24h=1.0, volume_24h=1000000,
    )
    assert score > 60

    # Overbought RSI should give low score
    score = analyzer._calculate_score(
        rsi=80, macd_data={"histogram": -0.5, "macd": -1, "signal": -0.5},
        bb=None, change_24h=-3.0, volume_24h=1000000,
    )
    assert score < 40


def test_recommendation(analyzer):
    assert analyzer._get_recommendation(75, 30) == "STRONG_BUY"
    assert analyzer._get_recommendation(65, 40) == "BUY"
    assert analyzer._get_recommendation(50, 50) == "HOLD"
    assert analyzer._get_recommendation(35, 60) == "SELL"
    assert analyzer._get_recommendation(20, 75) == "STRONG_SELL"
