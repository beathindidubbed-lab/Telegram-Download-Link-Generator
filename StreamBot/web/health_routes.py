"""
StreamBot/web/health_routes.py
Health check routes for UptimeRobot and other monitoring services.
Includes a beautiful Dark Mode dashboard.
"""

import logging
import time
import datetime
from aiohttp import web
from pyrogram import Client

logger = logging.getLogger(__name__)

# Import Var config
try:
    from StreamBot.config import Var
except ImportError:
    Var = None

routes = web.RouteTableDef()


def format_uptime(start_time_dt: datetime.datetime) -> str:
    """Format the uptime into a human-readable string."""
    if start_time_dt is None:
        return "N/A"
    
    if start_time_dt.tzinfo is None:
        start_time_dt = start_time_dt.replace(tzinfo=datetime.timezone.utc)
        
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - start_time_dt
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    uptime_str = ""
    if days > 0:
        uptime_str += f"{days}d "
    if hours > 0:
        uptime_str += f"{hours}h "
    if minutes > 0:
        uptime_str += f"{minutes}m "
    uptime_str += f"{seconds}s"
    return uptime_str.strip() if uptime_str else "0s"


# --- CSS STYLES (Defined separately to avoid Python f-string errors) ---
PAGE_STYLE = """
    :root {
        --bg-color: #0f0f0f;
        --card-bg: #1e1e1e;
        --text-primary: #ffffff;
        --text-secondary: #aaaaaa;
        --accent-color: #007bff;
        --accent-hover: #0056b3;
        --success: #28a745;
        --warning: #ffc107;
        --danger: #dc3545;
    }
    body {
        font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        background-color: var(--bg-color);
        color: var(--text-primary);
        margin: 0;
        padding: 0;
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }
    .container {
        width: 100%;
        max-width: 800px;
        padding: 20px;
    }
    .header {
        text-align: center;
        margin-bottom: 40px;
    }
    h1 {
        font-weight: 700;
        letter-spacing: -0.5px;
        margin-bottom: 10px;
        background: linear-gradient(45deg, #007bff, #00d2ff);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .status-badge {
        display: inline-block;
        padding: 8px 16px;
        border-radius: 50px;
        font-weight: 600;
        font-size: 0.9rem;
        background: rgba(40, 167, 69, 0.1);
        color: var(--success);
        border: 1px solid rgba(40, 167, 69, 0.2);
    }
    .status-badge.offline {
        background: rgba(220, 53, 69, 0.1);
        color: var(--danger);
        border-color: rgba(220, 53, 69, 0.2);
    }
    .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 20px;
        margin-bottom: 40px;
    }
    .card {
        background: var(--card-bg);
        padding: 25px;
        border-radius: 12px;
        border: 1px solid #333;
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .card:hover {
        transform: translateY(-5px);
        box-shadow: 0 10px 20px rgba(0,0,0,0.3);
        border-color: var(--accent-color);
    }
    .card-label {
        font-size: 0.85rem;
        color: var(--text-secondary);
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 10px;
    }
    .card-value {
        font-size: 1.5rem;
        font-weight: 700;
        color: var(--text-primary);
    }
    .footer {
        text-align: center;
        color: var(--text-secondary);
        font-size: 0.9rem;
        border-top: 1px solid #333;
        padding-top: 20px;
        width: 100%;
    }
    .footer a {
        color: var(--accent-color);
        text-decoration: none;
        margin: 0 10px;
        transition: color 0.2s;
    }
    .footer a:hover {
        color: var(--accent-hover);
        text-decoration: underline;
    }
"""

@routes.get("/health")
@routes.get("/api/info")
async def health_check_route(request: web.Request):
    """
    Comprehensive health check endpoint.
    Returns JSON status.
    """
    try:
        start_time = request.app.get('start_time') or request.app.get('bot_start_time')
        bot_client: Client = request.app.get('bot_client')
        
        status_code = 200
        status_msg = "healthy"
        bot_connected = False
        
        if bot_client and bot_client.is_connected:
            bot_connected = True
        else:
            status_code = 503
            status_msg = "unhealthy"

        response_data = {
            "status": status_msg,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "uptime": format_uptime(start_time),
            "bot_connected": bot_connected
        }
        return web.json_response(response_data, status=status_code)
        
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


@routes.get("/ping")
async def ping_route(request: web.Request):
    """Simple ping."""
    return web.json_response({"status": "ok", "message": "pong"})


@routes.head("/health")
@routes.head("/ping")
async def health_check_head(request: web.Request):
    return web.Response(status=200)


@routes.get("/status")
@routes.get("/") 
async def status_route(request: web.Request):
    """
    Home Page Dashboard (Dark Mode).
    """
    try:
        bot_client: Client = request.app.get('bot_client')
        start_time = request.app.get('start_time') or request.app.get('bot_start_time')
        
        # Determine Status
        if bot_client and bot_client.is_connected:
            status_text = "SYSTEM ONLINE"
            status_class = ""
            bot_username = getattr(bot_client.me, 'username', 'Unknown') if hasattr(bot_client, 'me') else 'Unknown'
        else:
            status_text = "SYSTEM OFFLINE"
            status_class = "offline"
            bot_username = "N/A"
        
        uptime_str = format_uptime(start_time) if start_time else "Unknown"
        
        # Get active streams safely
        try:
            from StreamBot.utils.stream_cleanup import stream_tracker
            active_streams = str(stream_tracker.get_active_count())
        except Exception:
            active_streams = "0"

        # Generate HTML (Using standard formatting)
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stream Bot Panel</title>
    <style>
        {PAGE_STYLE}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Telegram Stream Generator</h1>
            <div class="status-badge {status_class}">
                ● {status_text}
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-label">Bot Identity</div>
                <div class="card-value">@{bot_username}</div>
            </div>
            
            <div class="card">
                <div class="card-label">System Uptime</div>
                <div class="card-value">{uptime_str}</div>
            </div>
            
            <div class="card">
                <div class="card-label">Active Streams</div>
                <div class="card-value">{active_streams}</div>
            </div>
            
            <div class="card">
                <div class="card-label">Server Time (UTC)</div>
                <div class="card-value">{datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')}</div>
            </div>
        </div>

        <div class="footer">
            <p>Dashboard auto-refreshes every 30s</p>
            <a href="/health">Health Check</a> • 
            <a href="/api/info">API Info</a>
        </div>
    </div>
    <script>
        setTimeout(() => window.location.reload(), 30000);
    </script>
</body>
</html>
        """
        
        return web.Response(text=html_content, content_type='text/html')
        
    except Exception as e:
        logger.error(f"Status page error: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


__all__ = ['routes']
