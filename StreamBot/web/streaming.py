import logging
import asyncio
import math
from aiohttp import web
from pyrogram.errors import FloodWait
from StreamBot.config import Var
from StreamBot.utils.utils import decode_message_id, get_file_attr, VIDEO_MIME_TYPES, get_media_message
from StreamBot.utils.bandwidth import is_bandwidth_limit_exceeded, add_bandwidth_usage
from StreamBot.utils.stream_cleanup import stream_tracker, tracked_stream_response
from StreamBot.security.validator import validate_range_header, get_client_ip

logger = logging.getLogger(__name__)

async def stream_video_route(request: web.Request):
    """Handle video streaming requests with optimized streaming support."""
    client_manager = request.app.get('client_manager')
    if not client_manager:
        raise web.HTTPServiceUnavailable(text="Service configuration error.")

    encoded_id = request.match_info['encoded_id_str']
    
    if not encoded_id or len(encoded_id) > 100:
        raise web.HTTPBadRequest(text="Invalid stream link format.")
    
    message_id = decode_message_id(encoded_id)
    if message_id is None:
        raise web.HTTPBadRequest(text="Invalid or malformed stream link.")

    logger.info(f"Video stream request for message_id: {message_id} from {get_client_ip(request)}")

    # Check bandwidth limit
    if await is_bandwidth_limit_exceeded():
        raise web.HTTPServiceUnavailable(text="Service temporarily unavailable due to bandwidth limits.")

    try:
        streamer_client = await asyncio.wait_for(
            client_manager.get_streaming_client(),
            timeout=30
        )
        if not streamer_client or not streamer_client.is_connected:
            raise web.HTTPServiceUnavailable(text="Streaming service temporarily unavailable.")

        media_msg = await get_media_message(streamer_client, message_id)
        
        # Get file attributes
        file_id, file_name, file_size, file_mime_type, file_unique_id = get_file_attr(media_msg)
        
        if not file_id:
            raise web.HTTPNotFound(text="File not found or invalid.")

        # Check if file is a video using shared VIDEO_MIME_TYPES
        if file_mime_type not in VIDEO_MIME_TYPES:
            raise web.HTTPBadRequest(text="File is not a streamable video format.")

        # Get ByteStreamer
        byte_streamer = client_manager.get_streamer_for_client(streamer_client)
        if not byte_streamer:
            raise web.HTTPInternalServerError(text="Streaming service not available.")

        # Get file properties
        file_id_obj = await byte_streamer.get_file_properties(message_id)
        file_size = getattr(file_id_obj, 'file_size', file_size)

        if file_size == 0:
            raise web.HTTPBadRequest(text="Invalid video file.")

        # Streaming-optimized headers
        headers = {
            'Content-Type': file_mime_type,
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Connection': 'keep-alive',
            'Content-Disposition': 'inline'
        }

        # Handle range requests for video seeking - FIXED CALCULATION
        range_header = request.headers.get('Range')
        status_code = 200
        start_offset = 0
        end_offset = file_size - 1

        if range_header:
            logger.debug(f"Video range request for {message_id}: {range_header}")
            range_result = validate_range_header(range_header, file_size)
            if range_result is None:
                raise web.HTTPRequestRangeNotSatisfiable(
                    headers={'Content-Range': f'bytes */{file_size}'}
                )
            
            start_offset, end_offset = range_result
            
            # IMPORTANT FIX: Only limit response size for very large requests
            # Let the browser request what it needs, but prevent abuse
            requested_size = end_offset - start_offset + 1
            max_reasonable_request = 50 * 1024 * 1024  # 50MB max per request
            
            if requested_size > max_reasonable_request:
                # Only limit if the request is unreasonably large
                end_offset = start_offset + max_reasonable_request - 1
                # Make sure we don't exceed file size
                if end_offset >= file_size:
                    end_offset = file_size - 1
                logger.debug(f"Limited large range request from {requested_size} to {end_offset - start_offset + 1} bytes")
            
            headers['Content-Range'] = f'bytes {start_offset}-{end_offset}/{file_size}'
            headers['Content-Length'] = str(end_offset - start_offset + 1)
            status_code = 206
            logger.debug(f"Serving video range {start_offset}-{end_offset}/{file_size} for {message_id}")
        else:
            headers['Content-Length'] = str(file_size)
            logger.debug(f"Serving full video {file_size} bytes for {message_id}")

        response = web.StreamResponse(status=status_code, headers=headers)
        await response.prepare(request)

        # --- REVISED STREAMING LOGIC ---
        # Using Pyrogram's high-level stream_media for better reliability and seeking.
        chunk_size = 1024 * 1024  # Pyrogram's stream_media uses 1MB chunks.
        
        # Calculate the total bytes we need to serve for this request.
        bytes_to_serve = end_offset - start_offset + 1

        bytes_streamed = 0
        request_id = f"stream_{message_id}_{encoded_id[:10]}"
        max_retries = 3  # Increased retries for better reliability
        current_retry = 0

        async with tracked_stream_response(response, stream_tracker, request_id):
            while current_retry <= max_retries:
                try:
                    # Calculate remaining bytes to stream
                    remaining_bytes = bytes_to_serve - bytes_streamed
                    
                    if remaining_bytes <= 0:
                        logger.info(f"Video stream completed for {message_id}. Total: {bytes_streamed} bytes")
                        break
                    
                    # Calculate current position for resumption
                    current_byte_offset = start_offset + bytes_streamed
                    current_start_chunk = current_byte_offset // chunk_size
                    current_skip_bytes = current_byte_offset % chunk_size
                    
                    if bytes_streamed > 0:
                        logger.info(f"Resuming video stream for {message_id} at byte {current_byte_offset}")
                    
                    # Use Pyrogram's stream_media with chunk offset
                    media_stream = streamer_client.stream_media(
                        media_msg,
                        offset=current_start_chunk
                    )
                    
                    chunk_index = 0
                    consecutive_errors = 0
                    
                    async for chunk in media_stream:
                        if not chunk:
                            logger.debug(f"Empty chunk received for {message_id}, ending stream")
                            break

                        # Skip bytes in first chunk if resuming mid-chunk
                        if chunk_index == 0 and current_skip_bytes > 0:
                            chunk = chunk[current_skip_bytes:]

                        # Truncate last chunk if needed
                        if bytes_streamed + len(chunk) > bytes_to_serve:
                            chunk = chunk[:bytes_to_serve - bytes_streamed]

                        # Write chunk with improved error handling
                        try:
                            # Use a timeout for writing to prevent indefinite stalls
                            await asyncio.wait_for(response.write(chunk), timeout=30)
                            bytes_streamed += len(chunk)
                            consecutive_errors = 0  # Reset error counter on success
                            
                            # Log progress for large streams
                            if bytes_to_serve > 0 and bytes_streamed % (10 * 1024 * 1024) < len(chunk):  # Check around every 10MB mark
                                progress = (bytes_streamed / bytes_to_serve) * 100
                                logger.info(f"Video stream progress for {message_id}: {progress:.1f}%")
                                
                        except asyncio.TimeoutError:
                            logger.warning(f"Timeout writing chunk for {message_id}")
                            consecutive_errors += 1
                            if consecutive_errors >= 3:
                                logger.error(f"Too many consecutive write errors for {message_id}")
                                return response
                            await asyncio.sleep(1)
                            continue
                            
                        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                            logger.info(f"Client disconnected during video stream {message_id}")
                            return response
                            
                        except Exception as e:
                            logger.error(f"Error writing chunk for {message_id}: {e}")
                            consecutive_errors += 1
                            if consecutive_errors >= 3:
                                return response
                            await asyncio.sleep(1)
                            continue
                        
                        chunk_index += 1
                        
                        # Check if we've served all required bytes
                        if bytes_streamed >= bytes_to_serve:
                            break
                    
                    # Streaming completed successfully
                    logger.info(f"Video stream completed successfully for {message_id}")
                    break

                except FloodWait as e:
                    logger.warning(f"FloodWait during video stream {message_id}: {e.value}s")
                    
                    # Try alternative client first
                    alternative_client = await client_manager.get_alternative_streaming_client(streamer_client)
                    if alternative_client:
                        logger.info(f"Switching to alternative client for {message_id}")
                        streamer_client = alternative_client
                        # Re-fetch media message in case of client switch is necessary for Pyrogram's internal logic
                        media_msg = await get_media_message(streamer_client, message_id)
                        await asyncio.sleep(1)
                        continue
                    else:
                        # No alternative, wait briefly
                        wait_time = min(e.value, 30)  # Cap at 30 seconds
                        logger.info(f"Waiting {wait_time}s before retry")
                        await asyncio.sleep(wait_time)
                        continue

                except Exception as e:
                    current_retry += 1
                    logger.error(f"Error during video streaming {message_id} (attempt {current_retry}/{max_retries}): {e}", exc_info=True)
                    
                    if current_retry > max_retries:
                        logger.error(f"Max retries reached for {message_id}, aborting")
                        break
                    
                    # Try alternative client on non-FloodWait error
                    try:
                        alternative_client = await client_manager.get_alternative_streaming_client(streamer_client)
                        if alternative_client:
                            logger.info(f"Switching to alternative client after error")
                            streamer_client = alternative_client
                            # Re-fetch media message
                            media_msg = await get_media_message(streamer_client, message_id)
                    except Exception as alt_e:
                        logger.error(f"Error switching client: {alt_e}")
                    
                    # Exponential backoff
                    await asyncio.sleep(min(2 ** current_retry, 10))

        # Record bandwidth usage
        if bytes_streamed > 0:
            await add_bandwidth_usage(bytes_streamed)

        logger.info(f"Video stream completed for {message_id}: {bytes_streamed} bytes (expected: {bytes_to_serve})")
        return response

    except (web.HTTPNotFound, web.HTTPServiceUnavailable, web.HTTPBadRequest) as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error in video streaming {message_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text="Streaming error occurred.")
