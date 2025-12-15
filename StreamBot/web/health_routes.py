 StreamBot/web/health_routes.py
"""
Health check routes for UptimeRobot and other monitoring services.
Provides multiple endpoints for different monitoring needs.
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
async def health_check_route(request: web.Request):
    """
    Comprehensive health check endpoint for UptimeRobot and monitoring services.
    Returns detailed health status of the bot and its services.
    
    Response Codes:
    - 200: Service is healthy or degraded but operational
    - 503: Service is unhealthy or down
    
    Example Response:
    {
        "status": "healthy",
        "timestamp": "2024-12-14T10:30:00Z",
        "bot_status": "connected",
        "uptime": "2d 5h 30m 15s",
        "total_clients": 4,
        "connected_clients": 4,
        "database_status": "connected",
        "active_streams": 3,
        "bandwidth_used_gb": 45.3,
        "response_time_ms": 125.5
    }
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
    Minimal resource usage - ideal for frequent checks (1-minute intervals).
    
    This endpoint only checks if the web server is responding.
    Use /health for comprehensive service health checks.
    
    Response:
    {
        "status": "ok",
        "timestamp": "2024-12-14T10:30:00Z",
        "message": "pong"
    }
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
    More efficient for monitors that only need status code.
    
    Returns:
    - 200: Service is operational
    - 503: Service is down
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
async def status_route(request: web.Request):
    """
    Human-readable status page.
    Returns HTML with current bot status for browser viewing.
    """
    try:
        bot_client: Client = request.app.get('bot_client')
        start_time = request.app.get('start_time') or request.app.get('bot_start_time')
        
        # Get basic status
        if bot_client and bot_client.is_connected:
            status_emoji = "✅"
            status_text = "Online"
            bot_username = getattr(bot_client.me, 'username', 'Unknown') if hasattr(bot_client, 'me') else 'Unknown'
        else:
            status_emoji = "❌"
            status_text = "Offline"
            bot_username = "Unknown"
        
        uptime_str = format_uptime(start_time) if start_time else "Unknown"
        
        # Get active streams
        try:
            from StreamBot.utils.stream_cleanup import stream_tracker
            active_streams = stream_tracker.get_active_count()
        except Exception:
            active_streams = "N/A"
        
        # Build HTML response
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Bot Status</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            background: white;
            border-radius: 10px;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            margin-top: 0;
        }}
        .status {{
            font-size: 24px;
            margin: 20px 0;
        }}
        .info {{
            margin: 10px 0;
            padding: 10px;
            background: #f8f9fa;
            border-radius: 5px;
        }}
        .label {{
            font-weight: bold;
            color: #666;
        }}
        .footer {{
            margin-top: 20px;
            color: #999;
            font-size: 12px;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Telegram Bot Status</h1>
        <div class="status">
            {status_emoji} Status: <strong>{status_text}</strong>
        </div>
        <div class="info">
            <span class="label">Bot Username:</span> @{bot_username}
        </div>
        <div class="info">
            <span class="label">Uptime:</span> {uptime_str}
        </div>
        <div class="info">
            <span class="label">Active Streams:</span> {active_streams}
        </div>
        <div class="info">
            <span class="label">Last Updated:</span> {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
        </div>
        <div class="footer">
            Auto-refreshes every 30 seconds
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
