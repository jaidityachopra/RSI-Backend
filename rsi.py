import yfinance as yf
import pandas as pd
import ta
import requests
from datetime import datetime, date
from stock_list import stock_list as companies
from nsepython import nse_holidays
import os
import smtplib
from email.message import EmailMessage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import html
from functools import lru_cache

# Remove duplicates from companies list at the module level
UNIQUE_COMPANIES = list(set(companies))  # Convert to set then back to list to remove duplicates

# ---------------------------- SETTINGS ---------------------------- #
rsi_period = 14
pivot_lookback = 5

# Email Configuration - Set these as environment variables or update directly
EMAIL_CONFIG = {
    'smtp_server': 'smtp.gmail.com',  # Change based on your email provider
    'smtp_port': 587,
    'sender_email': os.getenv('SENDER_EMAIL', 'rsidivergencebot@gmail.com'),
    'sender_password': os.getenv('EMAIL_PASSWORD', 'zdptfzhjeznahkqf'),
    'recipient_email': os.getenv('RECIPIENT_EMAIL', 'jaidityachopra@gmail.com')
}
# ------------------------------------------------------------------ #

_cache_store = {}
_cache_date = None

def is_today(index_date):
    return index_date.date() == datetime.now().date()

# Cache NSE holidays only once at startup
NSE_HOLIDAYS = set(nse_holidays())  # Store as set for fast lookup

def is_nse_trading_day(check_date=None):
    if check_date is None:
        check_date = datetime.today().date()
    if check_date.weekday() >= 5:
        return False
    return str(check_date) not in NSE_HOLIDAYS

@lru_cache(maxsize=None) # Cache the function to avoid repeated downloads
def download_data(symbol):
    global _cache_store, _cache_date
    today = datetime.now().date()

    # Reset cache daily
    if _cache_date != today:
        _cache_store = {}
        _cache_date = today

    if symbol in _cache_store:
        return _cache_store[symbol]
    

    ticker = yf.Ticker(symbol)
    data = ticker.history(period='1y')
    if data.empty:
        raise ValueError(f"No data found for {symbol}")
    
    _cache_store[symbol] = data
    
    return data

def add_rsi(data, period):
    data['rsi'] = ta.momentum.RSIIndicator(data['Close'], window=period).rsi()
    return data

def find_pivot_lows(series, left=5, right=5):
    pivots = []
    for i in range(left, len(series) - right):
        if all(series.iloc[i] < series.iloc[i - j] for j in range(1, left + 1)) and \
           all(series.iloc[i] < series.iloc[i + j] for j in range(1, right + 1)):
            pivots.append(i)
    return pivots

def check_bullish_divergence(data, pivot_lows):
    divergences = []
    for i in range(1, len(pivot_lows)):
        curr, prev = pivot_lows[i], pivot_lows[i - 1]
        rsi_hl = data['rsi'].iloc[curr] > data['rsi'].iloc[prev]
        price_ll = data['Low'].iloc[curr] < data['Low'].iloc[prev]
        if rsi_hl and price_ll:
            divergences.append(curr)
    return divergences

@lru_cache(maxsize=None)
def get_preprocessed_data(symbol):
    data = download_data(symbol)
    data = add_rsi(data, rsi_period)
    pivot_lows = find_pivot_lows(data['rsi'], pivot_lookback, pivot_lookback)
    divergences = check_bullish_divergence(data, pivot_lows)
    return data, divergences

def send_whatsapp_message(api_key, phone_number, message):
    url = f"https://api.callmebot.com/whatsapp.php?phone={phone_number}&text={message}&apikey={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        print("WhatsApp message sent successfully!")
    else:
        print("Failed to send WhatsApp message:", response.text)

def scan_for_today_divergences():
    """Scan for divergences that occurred today"""
    if not is_nse_trading_day():
        print("Market is closed today. Exiting script.")
        return []
    
    today_divergences = []
    
    for symbol in UNIQUE_COMPANIES:  # Use unique companies list
        try:
            data = download_data(symbol)
            data = add_rsi(data, rsi_period)
            pivot_lows = find_pivot_lows(data['rsi'], pivot_lookback, pivot_lookback)
            divergences = check_bullish_divergence(data, pivot_lows)

            for idx in divergences:
                index_date = data.index[idx]
                if is_today(index_date):
                    rsi_val = data['rsi'].iloc[idx]
                    result = {
                        'symbol': symbol,
                        'date': index_date.strftime('%Y-%m-%d'),
                        'rsi': round(rsi_val, 2),
                        'close': round(data['Close'].iloc[idx], 2),
                        'low': round(data['Low'].iloc[idx], 2),
                        'high': round(data['High'].iloc[idx], 2),
                        'volume': int(data['Volume'].iloc[idx])
                    }
                    today_divergences.append(result)
                    print(f"Bullish RSI Divergence detected for {symbol} on {index_date.strftime('%Y-%m-%d')} | RSI: {rsi_val:.2f}")
        
        except Exception as e:
            print(f"Error processing {symbol}: {e}")
    
    return today_divergences

def get_bullish_divergence_results(target_date, symbols=None, progress_callback=None, use_next_open=False):
    """Get divergence results for a specific date with proper handling of missing future data"""
    results = []
    symbols = symbols if symbols else UNIQUE_COMPANIES  # Use unique companies list
    total = len(symbols)
    
    # Check if target date is today
    is_target_today = target_date == datetime.now().date()

    for i, symbol in enumerate(symbols):
        try:
            data, divergences = get_preprocessed_data(symbol)

            for idx in divergences:
                index_date = data.index[idx].date()
                if index_date == target_date:
                    rsi_val = data['rsi'].iloc[idx]
                    close_today = data['Close'].iloc[idx]
                    close_prev = data['Close'].iloc[idx - 1] if idx > 0 else None

                    # Get opening price of the next day (if available)
                    open_next_day = data['Open'].iloc[idx + 1] if idx + 1 < len(data) else None

                    # Select base price for return calculation
                    if use_next_open and open_next_day is not None:
                        base_price = open_next_day
                        price_basis = "Open Next Day"
                    else:
                        base_price = close_today
                        price_basis = "Close"

                    # Future returns based on selected base price
                    future_returns = {}
                    available_days = 0
                    
                    for j in range(1, 6):
                        if idx + j < len(data):
                            future_close = data['Close'].iloc[idx + j]
                            ret = ((future_close - base_price) / base_price) * 100
                            future_returns[f"Day+{j} Return (%)"] = round(ret, 2)
                            available_days = j
                        else:
                            future_returns[f"Day+{j} Return (%)"] = None  # Explicitly set to None

                    result = {
                        "Symbol": symbol,
                        "Prev Close": round(close_prev, 2) if close_prev else None,
                        "Divergence Close": round(close_today, 2),
                        "Open Next Day": round(open_next_day, 2) if open_next_day is not None else None,
                        "RSI": round(rsi_val, 2),
                        "Used Price": price_basis,
                        "Available Future Days": available_days,  # Track how many future days we have
                        "Is Today's Signal": is_target_today,  # Flag to identify today's signals
                        **future_returns
                    }

                    results.append(result)

        except Exception as e:
            print(f"Error processing {symbol}: {e}")
        
        if progress_callback:
            progress_callback(i + 1, total, symbol)

    return results



def format_volume(vol):
    return f"{vol / 100000:.2f}L"

def get_tradingview_link(symbol_with_suffix):
    if symbol_with_suffix.endswith(".NS"):
        exchange = "NSE"
    elif symbol_with_suffix.endswith(".BSE"):
        exchange = "BSE"
    else:
        return None  # or handle unknown exchange
    symbol = symbol_with_suffix.split('.')[0]
    return f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}"


def create_email_content(divergences_data):
    """Create HTML email content with divergence data"""
    print("New email template loaded")
    if not divergences_data:
        return "No bullish RSI divergences detected today.", "No bullish RSI divergences detected today."
    
    # Create HTML content
    html_content = f"""
    <html>
    <head>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #485563 0%, #29323c 100%);
                min-height: 100vh;
            }}
            .container {{
                max-width: 700px;
                margin: 0 auto;
                background-color: white;
                border-radius: 20px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.15);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #093028 0%, #237A57 100%);
                color: white;
                padding: 20px 30px;
                text-align: center;
                position: relative;
                overflow: hidden;
            }}
            .header::before {{
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: 
                    radial-gradient(circle at 20% 20%, rgba(255,255,255,0.1) 0%, transparent 50%),
                    radial-gradient(circle at 80% 80%, rgba(255,255,255,0.05) 0%, transparent 50%),
                    radial-gradient(circle at 40% 40%, rgba(255,255,255,0.03) 0%, transparent 50%);
                opacity: 0.8;
            }}
            .header-content {{
                position: relative;
                z-index: 1;
            }}
            .alert-section {{
                margin-top: 30px;
                padding: 20px;
                background: rgba(255,255,255,0.1);
                border-radius: 15px;
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255,255,255,0.2);
            }}
            .alert-title {{
                font-size: 24px;
                font-weight: 700;
                margin: 0 0 10px 0;
                color: #fff;
                text-align: center;
            }}

            .timestamp {{
                font-size: 14px;
                opacity: 0.9;
                margin: 0;
                color: rgba(255,255,255,0.8);
            }}
            .content {{
                padding: 40px 30px;
            }}
            .summary {{
                background: linear-gradient(135deg, #f8fdfc 0%, #e8f8f5 100%);
                border-left: 4px solid #237A57;
                padding: 25px;
                margin-bottom: 30px;
                border-radius: 12px;
                font-size: 16px;
                color: #2c3e50;
                box-shadow: 0 5px 15px rgba(0,0,0,0.05);
            }}
            .summary strong {{
                color: #237A57;
                font-size: 20px;
                font-weight: 700;
            }}
            .summary-icon {{
                font-size: 24px;
                margin-right: 10px;
                vertical-align: middle;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
                background: white;
                border-radius: 15px;
                overflow: hidden;
                box-shadow: 0 8px 25px rgba(0,0,0,0.1);
            }}
            .table-wrapper {{
            overflow-x: auto;
            -ms-overflow-style: none;  /* IE and Edge */
            -webkit-overflow-scrolling: touch;
            touch-action: pan-x; /* key for mobile swipe */
            }}

            th {{
                background: linear-gradient(135deg, #44A08D 0%, #093637 100%);
                color: white;
                padding: 20px 15px;
                text-align: left;
                font-weight: 600;
                font-size: 14px;
                letter-spacing: 0.5px;
                text-transform: uppercase;
                border-bottom: 2px solid rgba(255,255,255,0.1);
            }}
            td {{
                padding: 18px 15px;
                border-bottom: 1px solid #f1f5f9;
                font-size: 14px;
                transition: all 0.3s ease;
            }}
            tr:hover td {{
                background: linear-gradient(135deg, #f8fdfc 0%, #e8f8f5 100%);
                transform: translateY(-1px);
            }}
            tr:last-child td {{
                border-bottom: none;
            }}
            .symbol {{
                font-weight: 700;
                color: #093637;
                font-size: 16px;
                background: linear-gradient(135deg, #093637 0%, #44A08D 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }}
            .rsi {{
                color: #24243e;
                font-weight: 700;
                font-size: 15px;
            }}
            .price {{
                color: #27ae60;
                font-weight: 600;
                font-size: 15px;
                }}
            .volume {{
                color: #3498db;
                font-weight: 600;
                font-size: 15px;
            }}
            .footer {{
                background: linear-gradient(135deg, #f8fdfc 0%, #e8f8f5 100%);
                padding: 35px 30px;
                border-top: 1px solid #e8f8f5;
                margin-top: 30px;
            }}
            .footer-title {{
                color: #2c3e50;
                font-size: 20px;
                font-weight: 700;
                margin-bottom: 15px;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .footer-content {{
                color: #5a6c7d;
                font-size: 15px;
                line-height: 1.7;
                margin-bottom: 20px;
            }}
            .disclaimer {{
                color: #7f8c8d;
                font-size: 12px;
                font-style: italic;
                padding: 20px;
                background: rgba(255,255,255,0.8);
                border-radius: 10px;
                border-left: 4px solid #667eea;
                box-shadow: 0 3px 10px rgba(0,0,0,0.05);
            }}
            .brand-footer {{
                text-align: center;
                padding: 25px;
                background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
                color: white;
                font-size: 13px;
                letter-spacing: 1px;
            }}
            .brand-name {{
                font-weight: 700;
                color: #237A57;
                text-shadow: 0 2px 4px rgba(0,0,0,0.2);
            }}
            .metric-highlight {{
                background: linear-gradient(135deg, #093637 0%, #44A08D 100%);
                color: white;
                padding: 4px 8px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="header-content">
                    <div class="alert-section">
                        <div class="alert-title" style="text-align: center;">
                        📈 Bullish Divergence Alert 🎯
                        </div>
                        <p class="timestamp">{datetime.now().strftime('%A, %B %d, %Y at %I:%M %p')}</p>
                    </div>
                </div>
            </div>
            
            <div class="content">
                <div class="summary">
                    <span class="summary-icon">🚀</span>
                    <strong>{len(divergences_data)}</strong> bullish RSI divergence signal{'s' if len(divergences_data) > 1 else ''} detected today! These stocks are showing potential reversal patterns with strong technical indicators.
                </div>
                
                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>RSI</th>
                                <th>Close Price</th>
                                <th>Low Price</th>
                                <th>High Price</th>
                                <th>Volume</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    for item in divergences_data:
        html_content += f"""
                        <tr>
                            <td class="symbol">
                                <a href="{get_tradingview_link(item['symbol'])}" 
                                target="_blank" 
                                style="text-decoration: none; color: #093637; font-weight: 700;">
                                {html.escape(item['symbol'].split('.')[0])}
                                </a>
                            </td>

                            <td class="rsi">{item['rsi']}</td>
                            <td class="price">₹{item['close']}</td>
                            <td class="price">₹{item['low']}</td>
                            <td class="price">₹{item['high']}</td>
                            <td class="volume">{format_volume(item['volume'])}</td>
                        </tr>
        """
    
    html_content += """
                        </tbody>
                    </table>
                </div>
            </div>
            
            <div class="footer">
                <div class="footer-title">
                    💡 What is RSI Bullish Divergence?
                </div>
                <div class="footer-content">
                    RSI Bullish Divergence occurs when the stock price makes a lower low, but the RSI makes a higher low. This technical pattern suggests that selling pressure is weakening and a potential upward price movement may follow. It's considered a <span class="metric-highlight">Strong Buy Signal</span> by professional technical analysts.
                </div>
                <div class="disclaimer">
                    📒 <strong>Note to Self:</strong> Trust the pattern, but verify the context. This tool is a guide, not a guarantee.
                </div>
            </div>
            
            <div class="brand-footer">
                Built with ❤️ by a father-son duo over countless weekend experiments.
            </div>
        </div>
    </body>
    </html>
    """
    
    # Create plain text version
    text_content = f"""
========================================
RSI DIVERGENCE INDICATOR
========================================

BULLISH DIVERGENCE ALERT
{datetime.now().strftime('%A, %B %d, %Y at %I:%M %p')}

🎯 DETECTED {len(divergences_data)} BULLISH RSI DIVERGENCE SIGNAL{'S' if len(divergences_data) > 1 else ''}

Stock Details:
"""
    
    for i, item in enumerate(divergences_data, 1):
        text_content += f"""
{i}. {item['symbol']}
   RSI: {item['rsi']}
   Close Price: ₹{item['close']}
   Low Price: ₹{item['low']}
   High Price: ₹{item['high']}
   Volume: {format_volume(item['volume'])}
   
"""
    
    text_content += """
========================================
WHAT IS RSI BULLISH DIVERGENCE?
========================================

RSI Bullish Divergence occurs when the stock price makes a lower low, but the RSI makes a higher low. This technical pattern suggests that selling pressure is weakening and a potential upward price movement may follow.

⚠️ DISCLAIMER: This is an automated technical analysis alert for educational purposes only. Please conduct your own research and consult with a qualified financial advisor before making any investment decisions. Past performance does not guarantee future results.

Powered by RSI DIVERGENCE INDICATOR
Professional Technical Analysis Solutions
"""
    
    return html_content, text_content


def send_email_notification(divergences_data):
    """Send email notification with divergence data"""
    try:
        # Check if email configuration is set
        if EMAIL_CONFIG['sender_email'] != 'rsidivergencebot@gmail.com':
            print("Email configuration not set. Please update EMAIL_CONFIG in the code.")
            return False
        
        # Create email content
        html_content, text_content = create_email_content(divergences_data)
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"RSI Divergence Alert - {len(divergences_data)} Signal(s) - {datetime.now().strftime('%Y-%m-%d')}"
        msg['From'] = EMAIL_CONFIG['sender_email']
        msg['To'] = EMAIL_CONFIG['recipient_email']
        
        # Create text and HTML parts
        text_part = MIMEText(text_content, 'plain')
        html_part = MIMEText(html_content, 'html')
        
        # Attach parts
        msg.attach(text_part)
        msg.attach(html_part)
        
        # Send email
        with smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port']) as server:
            server.starttls()
            server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
            server.send_message(msg)
        
        print(f"Email notification sent successfully to {EMAIL_CONFIG['recipient_email']}")
        return True
        
    except Exception as e:
        print(f"Failed to send email notification: {e}")
        return False


## Uncomment the below block to run the script directly
# Example usage for today's scan
if __name__ == "__main__":
    
    if not is_nse_trading_day():
        print(f" {datetime.now().date()} is not a trading day. Exiting script.")
        exit()

    print("="*50)
    print("RSI Bullish Divergence Scanner")
    print("="*50)
    
    print("Scanning for today's bullish divergences...")
    today_results = scan_for_today_divergences()
    
    if today_results:
        print(f"\n Found {len(today_results)} bullish divergence(s) today:")
        print("-" * 50)
        for result in today_results:
            print(f" {result['symbol']}: RSI {result['rsi']}, Close: ₹{result['close']}, Low: ₹{result['low']}")
        
        # Send email notification
        print("\n Sending email notification...")
        email_sent = send_email_notification(today_results)
        
        if email_sent:
            print("Email notification sent successfully!")
        else:
            print("Failed to send email notification.")
            
    else:
        print("No bullish divergences found today.")
        
        # Optionally send a "no signals" email
        # send_email_notification([])  # Uncomment if you want to receive emails even when no signals are found
