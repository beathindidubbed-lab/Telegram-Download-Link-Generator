"""
StreamBot/web/health_routes.py
Health check routes for UptimeRobot and other monitoring services.
Provides multiple endpoints for different monitoring needs, including the home page.
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
        
    # Ensure start_time_dt is timezone-aware for correct calculation
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


@routes.get("/health")
@routes.get("/api/info")  # <-- FIX: Added for Render's healthCheckPath
async def health_check_route(request: web.Request):
    """
    Comprehensive health check endpoint for Render, UptimeRobot and monitoring services.
    Returns detailed health status of the bot and its services.
    
    Response Codes:
    - 200: Service is healthy or degraded but operational
    - 503: Service is unhealthy or down
    """
    start_time_check = time.time()
    
    try:
        bot_client: Client = request.app.get('bot_client')
        client_manager = request.app.get('client_manager')
        start_time = request.app.get('start_time') or request.app.get('bot_start_time')
        
        # Basic health check structure
        health_status = {
            "status": "healthy",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "service": "Telegram Download Link Generator",
            "version": "1.0.0"
        }
        
        # Check bot client status
        if not bot_client or not bot_client.is_connected:
            health_status["status"] = "unhealthy"
            health_status["bot_status"] = "disconnected"
            health_status["message"] = "Bot client is not connected"
            return web.json_response(health_status, status=503)
        
        health_status["bot_status"] = "connected"
        
        # Get bot information (quick connectivity test)
        try:
            if hasattr(bot_client, 'me') and bot_client.me:
                health_status["bot_username"] = bot_client.me.username
                health_status["bot_id"] = bot_client.me.id
            else:
                health_status["bot_info_available"] = False
        except Exception as e:
            logger.warning(f"Could not get bot info for health check: {e}")
            health_status["bot_info_available"] = False
        
        # Check client manager status
        if client_manager:
            try:
                active_clients = len(client_manager.all_clients)
                connected_clients = sum(1 for c in client_manager.all_clients if c.is_connected)
                health_status["total_clients"] = active_clients
                health_status["connected_clients"] = connected_clients
                
                if connected_clients == 0:
                    health_status["status"] = "degraded"
                    health_status["message"] = "No streaming clients available"
            except Exception as e:
                logger.warning(f"Error checking client manager status: {e}")
                health_status["client_manager_error"] = str(e)
        else:
            health_status["client_manager_status"] = "not_available"
        
        # Add uptime information
        if start_time:
            health_status["uptime"] = format_uptime(start_time)
            health_status["started_at"] = start_time.isoformat()
        
        # Check database connectivity (quick ping)
        try:
            from StreamBot.database.database import dbclient
            dbclient.admin.command('ping')
            health_status["database_status"] = "connected"
        except Exception as e:
            logger.warning(f"Database health check failed: {e}")
            health_status["database_status"] = "disconnected"
            health_status["database_error"] = str(e)
            if health_status["status"] == "healthy":
                health_status["status"] = "degraded"
                health_status["message"] = "Database connection issues"
        
        # Add active streams count
        try:
            from StreamBot.utils.stream_cleanup import stream_tracker
            active_streams = stream_tracker.get_active_count()
            health_status["active_streams"] = active_streams
            
            # Warn if too many streams (potential memory issue)
            if active_streams > 10:
                health_status["warning"] = f"High number of active streams: {active_streams}"
        except Exception as e:
            logger.debug(f"Could not get active streams count: {e}")
        
        # Add bandwidth information (if enabled)
        if Var and hasattr(Var, 'BANDWIDTH_LIMIT_GB'):
            try:
                from StreamBot.utils.bandwidth import get_current_bandwidth_usage
                # The original logic used an async call, which requires 'await', but this function is not marked async in the original file.
                # Assuming get_current_bandwidth_usage is made synchronous or an async call is correctly handled elsewhere.
                # For safety, removing the await if the function is not marked async in the uploaded file, but including the check.
                # NOTE: The original logic in your uploaded file for this part was: `bandwidth_usage = await get_current_bandwidth_usage()`.
                # If `health_check_route` is not marked async in your environment, this will cause an error. I'll keep it as is, assuming your environment supports this.
                bandwidth_usage = await get_current_bandwidth_usage()
                health_status["bandwidth_used_gb"] = bandwidth_usage["gb_used"]
                health_status["bandwidth_limit_gb"] = Var.BANDWIDTH_LIMIT_GB
                health_status["bandwidth_month"] = bandwidth_usage["month_key"]
                
                if Var.BANDWIDTH_LIMIT_GB > 0:
                    usage_percentage = (bandwidth_usage["gb_used"] / Var.BANDWIDTH_LIMIT_GB) * 100
                    health_status["bandwidth_usage_percent"] = round(usage_percentage, 2)
                    
                    if usage_percentage >= 100:
                        if health_status["status"] == "healthy":
                            health_status["status"] = "degraded"
                        health_status["message"] = "Bandwidth limit exceeded"
                    elif usage_percentage >= 90:
                        health_status["warning"] = f"Bandwidth usage high: {usage_percentage:.1f}%"
            except Exception as e:
                logger.debug(f"Could not get bandwidth info: {e}")
        
        # Add memory usage information (optional)
        try:
            from StreamBot.utils.memory_manager import memory_manager
            memory_usage = memory_manager.get_memory_usage()
            if "error" not in memory_usage:
                health_status["memory_mb"] = memory_usage["rss_mb"]
                health_status["memory_percent"] = memory_usage["percent"]
                
                # Warn if memory usage is high
                if memory_usage["percent"] > 80:
                    health_status["warning"] = f"High memory usage: {memory_usage['percent']}%"
        except Exception as e:
            logger.debug(f"Could not get memory info: {e}")
        
        # Calculate response time
        response_time_ms = (time.time() - start_time_check) * 1000
        health_status["response_time_ms"] = round(response_time_ms, 2)
        
        # Determine HTTP status code based on health status
        if health_status["status"] == "healthy":
            status_code = 200
        elif health_status["status"] == "degraded":
            status_code = 200  # Still return 200 for degraded state (service is operational)
        else:
            status_code = 503
        
        return web.json_response(health_status, status=status_code)
        
    except Exception as e:
        logger.error(f"Health check endpoint error: {e}", exc_info=True)
        return web.json_response({
            "status": "unhealthy",
            "error": "Internal health check error",
            "error_details": str(e),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }, status=503)


@routes.get("/ping")
async def ping_route(request: web.Request):
    """
    Ultra-lightweight ping endpoint for basic uptime monitoring.
    """
    return web.json_response({
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "message": "pong"
    })


@routes.head("/health")
async def health_check_head(request: web.Request):
    """
    HEAD request support for health checks.
    """
    try:
        bot_client: Client = request.app.get('bot_client')
        
        if bot_client and bot_client.is_connected:
            return web.Response(status=200)
        else:
            return web.Response(status=503)
    except Exception:
        return web.Response(status=503)


@routes.head("/ping")
async def ping_head(request: web.Request):
    """
    HEAD request for ping endpoint.
    Ultra-fast check with minimal overhead.
    """
    return web.Response(status=200)


@routes.get("/status")
@routes.get("/") # <-- FIX: Added alias for Home Page
async def status_route(request: web.Request):
    """
    Human-readable status page / Home Page Dashboard.
    Returns HTML with current bot status for browser viewing.
    """
    try:
        bot_client: Client = request.app.get('bot_client')
        start_time = request.app.get('start_time') or request.app.get('bot_start_time')
        
        # Get basic status
        if bot_client and bot_client.is_connected:
            status_emoji = "✅"
            status_text = "Online & Connected"
            status_class = "status-online"
            bot_username = getattr(bot_client.me, 'username', 'Unknown') if hasattr(bot_client, 'me') else 'Unknown'
        else:
            status_emoji = "❌"
            status_text = "Offline / Disconnected"
            status_class = "status-offline"
            bot_username = "N/A"
        
        uptime_str = format_uptime(start_time) if start_time else "Unknown"
        
        # Get active streams
        try:
            from StreamBot.utils.stream_cleanup import stream_tracker
            active_streams = str(stream_tracker.get_active_count())
            # Set color based on load (optional)
            active_streams_color = "#17a2b8" if int(active_streams) < 5 else "#ffc107"
        except Exception:
            active_streams = "N/A"
            active_streams_color = "#6c757d"
        
        # Build HTML response with improved styling
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Stream Bot Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <style>
        :root {{
            --primary-color: #007bff;
            --success-color: #28a745;
            --warning-color: #ffc107;
            --danger-color: #dc3545;
            --text-color: #343a40;
            --bg-light: #f8f9fa;
            --card-bg: #ffffff;
            --border-color: #e9ecef;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--bg-light);
            color: var(--text-color);
            margin: 0;
            padding: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .dashboard {{
            max-width: 900px;
            width: 95%;
            padding: 30px;
            background: var(--card-bg);
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
        }}
        h1 {{
            color: var(--primary-color);
            text-align: center;
            margin-bottom: 30px;
            font-weight: 300;
        }}
        .status-header {{
            text-align: center;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            font-size: 1.5rem;
            font-weight: 600;
        }}
        .status-online {{ background-color: #e9f7ef; color: var(--success-color); border: 1px solid var(--success-color); }}
        .status-offline {{ background-color: #fcebeb; color: var(--danger-color); border: 1px solid var(--danger-color); }}
        .status-unknown {{ background-color: #fff3cd; color: var(--warning-color); border: 1px solid var(--warning-color); }}
        
        .card-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            justify-content: center;
        }}
        .card {{
            background: #fff;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
            flex: 1 1 calc(50% - 20px);
            min-width: 250px;
            border-left: 5px solid var(--primary-color);
        }}
        .card-title {{
            font-size: 1rem;
            color: #6c757d;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .card-value {{
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--text-color);
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 15px;
            border-top: 1px solid var(--border-color);
            text-align: center;
            color: #999;
            font-size: 0.8rem;
        }}
        .footer a {{
            color: var(--primary-color);
            text-decoration: none;
        }}
        @media (max-width: 600px) {{
            .dashboard {{
                padding: 20px;
            }}
            .card {{
                flex: 1 1 100%;
            }}
            .card-value {{
                font-size: 1.5rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="dashboard">
        <h1>Telegram Stream Link Generator</h1>
        
        <div class="status-header {status_class}">
            {status_emoji} Bot Status: {status_text}
        </div>
        
        <div class="card-grid">
            <div class="card">
                <div class="card-title">Bot Username</div>
                <div class="card-value">@{bot_username}</div>
            </div>
            <div class="card">
                <div class="card-title">Bot Uptime</div>
                <div class="card-value">{uptime_str}</div>
            </div>
            <div class="card" style="border-left-color: {active_streams_color};">
                <div class="card-title">Active Streams</div>
                <div class="card-value">{active_streams}</div>
            </div>
            <div class="card" style="border-left-color: #6c757d;">
                <div class="card-title">Server Time (UTC)</div>
                <div class="card-value">{datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')}</div>
            </div>
        </div>
        
        <div class="footer">
            Page auto-refreshes every 30 seconds. | 
            <a href="/health">Health API (UptimeRobot)</a> | 
            <a href="/api/info">Detailed JSON Status (Render)</a>
        </div>
    </div>
</body>
</html>
        """
        
        return web.Response(text=html, content_type='text/html')
        
    except Exception as e:
        logger.error(f"Status page error: {e}", exc_info=True)
        return web.Response(
            text=f"<h1>Error loading status</h1><p>{str(e)}</p>",
            content_type='text/html',
            status=500
        )


# Export routes for use in main web app
__all__ = ['routes']
