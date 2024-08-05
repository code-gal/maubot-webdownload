from maubot import Plugin, MessageEvent
from maubot.handlers import command, event
from mautrix.types import EventType, MessageType, VideoInfo, AudioInfo, ImageInfo, FileInfo, RelatesTo, RelationType, InReplyTo, EventID, MediaRepoConfig
import re
from urllib.parse import urlparse, unquote_plus
from os.path import basename, splitext
from mimetypes import guess_type
import aiohttp
import asyncio
import struct
import io
from tinytag import TinyTag

from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

from urldownload.DBManager import DBManager
from mautrix.util.async_db import UpgradeTable

from .Config import Config
from .dataclass.Attachment import Attachment
from .migrations import upgrade_table

from hashlib import sha512

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("command_prefix")
        helper.copy("whitelist")
        helper.copy("url_regex")
        helper.copy("mimetype_regex")
        helper.copy("extension_regex")
    
class URLDownloadBot(Plugin):
    dbm: DBManager
    config: Config

    @classmethod
    def get_config_class(cls) -> type[BaseProxyConfig]:
        return Config

    @classmethod
    def get_db_upgrade_table(cls) -> UpgradeTable:
        return upgrade_table

    def get_command_name(self) -> str:
        return self.config["command_prefix"]

    def is_whitelisted(self, mxid) -> bool:
        for whitelist_entry in self.config["whitelist"]:
            if re.fullmatch(whitelist_entry, mxid):
                return True
        return False

    def get_url_regex(self) -> str:
        return self.config["url_regex"]

    def get_mimetype_regex(self) -> str:
        return self.config["mimetype_regex"]

    def get_extension_regex(self) -> str:
        return self.config["extension_regex"]

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.dbm = DBManager(self.database)

    @command.new(name=get_command_name, require_subcommand=True)
    async def base_command(self, evt: MessageEvent) -> None:
        pass

    @base_command.subcommand(help="Enable downloading of URLs in this chat")
    async def enable(self, evt: MessageEvent) -> None:
        await self.dbm.set_enabled_in_room(evt.room_id, True)
        await self.client.send_notice(evt.room_id, "URLDownloader enabled!")

    @base_command.subcommand(help="Disable downloading of URLs in this chat")
    async def disable(self, evt: MessageEvent) -> None:
        await self.dbm.set_enabled_in_room(evt.room_id, False)
        await self.client.send_notice(evt.room_id, "URLDownloader disabled!")

    @base_command.subcommand(help="Get status of URLDownloader in this chat")
    async def status(self, evt: MessageEvent) -> None:
        enabled = await self.dbm.is_enabled_in_room(evt.room_id)
        debug = await self.dbm.is_debug_in_room(evt.room_id)
        await self.client.send_notice(evt.room_id, f"Enabled: {enabled} Debug: {debug}")

    @base_command.subcommand(help="Manage or get debug status in this room")
    @command.argument("state", "State of debug mode", required=False)
    async def debug(self, evt: MessageEvent, state:bool|None) -> None:
        if state is None or len(state) == 0:
            debug = await self.dbm.is_debug_in_room(evt.room_id)
            await self.client.send_notice(evt.room_id, f"Debug: {debug}")
        else:
            state = state.lower() in ['true', '1', 't', 'y', 'yes']
            await self.dbm.set_debug_in_room(evt.room_id, state)
            await self.client.send_notice(evt.room_id, f"Debug: {state}")

    async def get_file_info(self, session, url, evt, debug):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60), allow_redirects=True) as response:
                if response.status == 429:  # Too Many Requests
                    await evt.respond(f"Rate limit exceeded for URL {url}. Skipping.")
                    return None

                headers = response.headers
                content_type = headers.get("Content-Type")
                content_length = headers.get("Content-Length")
                content_disposition = headers.get("Content-Disposition")

                filename = None
                if content_disposition:
                    match = re.search(r'filename\*?="?([^"]+)"?', content_disposition)
                    if match:
                        filename = unquote_plus(match.group(1))

                if not filename:
                    filename = unquote_plus(basename(urlparse(url).path))

                extension = splitext(filename)[1]
                mimetype = content_type
                if mimetype is None or mimetype == 'application/octet-stream':
                    mimetype = guess_type(filename)[0]
                    
                # If we couldn't get the content length, we'll need to download the file
                file_size = int(content_length) if content_length else 0

                if debug:
                    await evt.respond(f"[DEBUG] Filename: {filename}, Determined MIME type: {mimetype} and File_Size: {file_size}")

                return {
                    "filename": filename,
                    "mimetype": mimetype,
                    "extension": extension,
                    "size": file_size
                }
        except Exception as e:
            if debug:
                await evt.respond(f"[DEBUG] Error in get_file_info: {str(e)}")
            return None

    async def download_with_progress(self, session, url, evt, debug, size_limit):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=7200, connect=60)) as response:
                if debug:
                    await evt.respond(f"[DEBUG] Starting download")

                media_data = b""
                start_time = asyncio.get_event_loop().time()
                async for chunk in response.content.iter_chunked(8192):
                    media_data += chunk
                    if len(media_data) > size_limit:
                        await evt.respond(f"File size exceeds limit ({size_limit} bytes). Stop downloading.")
                        return None
                    current_time = asyncio.get_event_loop().time()
                    if current_time - start_time > 7200:  # 2 hours
                        await evt.respond("Download time exceeded 2 hours. Download cancelled.")
                        return None

            return media_data
        except asyncio.TimeoutError:
            if debug:
                await evt.respond("[DEBUG] Connection timed out while downloading the file.")
            return None
        except Exception as e:
            if debug:
                await evt.respond(f"[DEBUG] An error occurred while downloading: {str(e)}")
            return None
    
    def get_jpeg_size_from_bytes(self, data, evt, debug):
        try:
            f = io.BytesIO(data)
            f.seek(0)
            size = 2
            ftype = 0
            while not 0xc0 <= ftype <= 0xcf:
                f.seek(size, 1)
                byte = f.read(1)
                while ord(byte) == 0xff:
                    byte = f.read(1)
                ftype = ord(byte)
                size = struct.unpack('>H', f.read(2))[0] - 2
            f.seek(1, 1)
            height, width = struct.unpack('>HH', f.read(4))
            return width, height
        except Exception as e:
            if debug:
                asyncio.create_task(evt.respond(f"[DEBUG] An Error getting picture size information: {str(e)}"))
            return None, None
        
    async def get_upload_size(self, evt, debug):
        try:
            server_config = await self.client.get_media_repo_config()

            if isinstance(server_config, MediaRepoConfig):
                return int(server_config.upload_size)
            else:
                return 1024 * 1024 * 50
        
        except Exception as e:
            if debug:
                await evt.respond(f"[DEBUG] An error occurred while get matrix server config: {str(e)}")
            return 1024 * 1024 * 50

    async def process_url(self, group, evt, debug, relates_to_content):
        try:
            async with aiohttp.ClientSession() as session:
                file_info = await self.get_file_info(session, group, evt, debug)
                
                if file_info is None:
                    return

                if not (re.match(self.get_mimetype_regex(), file_info["mimetype"]) or re.match(self.get_extension_regex(), file_info["extension"])):
                    if debug:
                        await evt.respond(f"[DEBUG] File type not allowed. Skipping download.")
                    return

                file_size = file_info["size"]
                size_limit = await self.get_upload_size(evt, debug)
                content = None
                if file_size == 0:
                    content = await self.download_with_progress(session, group, evt, debug, size_limit)
                    if content is None:
                        return
                    file_size = len(content)

                if file_size > size_limit:
                    await evt.respond(f"File size ({file_size} bytes) exceeds limit ({size_limit} bytes). Skipping download.")
                    return

                # If we haven't downloaded the content yet, do it now
                if content is None:
                    content = await self.download_with_progress(session, group, evt, debug, size_limit)
                    if content is None:
                        return  # Skip further processing if download failed or was cancelled
                    file_size = len(content)
                
                mimetype = file_info["mimetype"]
                is_video = mimetype.startswith('video/')
                is_audio = mimetype.startswith('audio/') or mimetype in ['application/ogg']
                is_image = mimetype.startswith('image/')
                
                sha512sum = sha512(content).hexdigest()
                attachment = await self.dbm.get_attachment(sha512sum)

                if attachment is None:
                    if debug:
                        await evt.respond(f"[DEBUG] First time encountering this attachment. Postprocessing.")
                    attachment = Attachment()
                    attachment.sha512sum = sha512sum
                    attachment.size = file_size
                    attachment.mimetype = mimetype
                    attachment.url = group

                    try:
                        # is_document = mimetype.startswith('application/') and attachment.mimetype != 'application/ogg'
                        # # Use OpenCV Process video files
                        if is_video:
                            filename = file_info["filename"]
                            # Check for (numberxnumber) pattern in the filename
                            hw_match = re.search(r'[-_ ](\d{1,4})x(\d{1,4})', filename)
                            if hw_match:
                                attachment.width = int(hw_match.group(1))
                                attachment.height = int(hw_match.group(2))
                            # Check if filename starts with "tiktok"
                            elif filename.lower().startswith("tiktok"):
                                attachment.width = 1080
                                attachment.height = 1920
                            else:
                                attachment.width = 1920
                                attachment.height = 1080
                        #     video_file = 'temp_video.mp4'
                        #     with open(video_file, 'wb') as f:
                        #         f.write(content)
                        #     cap = cv2.VideoCapture(video_file)
                        #     if cap.isOpened():
                        #         attachment.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        #         attachment.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        #         # CAP_PROP_POS_MSEC 获取的是视频当前帧的时间戳，而不是视频的总时长
                        #         # 用 CAP_PROP_FRAME_COUNT 和 CAP_PROP_FPS 计算总时长
                        #         frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                        #         fps = cap.get(cv2.CAP_PROP_FPS)
                        #         if fps > 0:
                        #             attachment.duration = int((frame_count / fps) * 1000)  # 转换为毫秒
                        #     cap.release()
                        
                        # Process audio files
                        elif is_audio:
                            # audio_file = 'temp_audio.mp3'
                            # with open(audio_file, 'wb') as f:
                            #     f.write(content)
                            # tag = TinyTag.get(audio_file)

                            audio_tag = TinyTag.get(file_obj=io.BytesIO(content))
                            if audio_tag:
                                attachment.duration = int(audio_tag.duration * 1000)  # Convert to milliseconds
                        
                        # Process image files
                        elif is_image:
                            width, height = self.get_jpeg_size_from_bytes(content, evt, debug)
                            if width:
                                attachment.width = width
                            if height:
                                attachment.height = height
                    
                    except Exception as ex:
                        if debug:
                            await evt.respond(f"[DEBUG] An error occurred during postprocessing: {ex}")

                    try:
                        attachment.uri = await self.client.upload_media(
                            data=content,
                            mime_type=attachment.mimetype,
                            filename=file_info["filename"],
                            size=attachment.size
                        )
                        if debug:
                            await evt.respond(f"[DEBUG] Upload File URI: {attachment.uri}")
                        
                        # # 获取缩略图（仅对视频、音频和文档）
                        # if is_video or is_audio or is_document: 
                        #     try:   
                        #         thumbnail_process = await self.client.download_thumbnail(
                        #             url=attachment.uri,
                        #             width=640,
                        #             height=480,
                        #             resize_method="scale",
                        #             allow_remote=None,  # 显式设置为 False，防止服务器尝试获取远程资源
                        #             timeout_ms=10000     # 显式传递 None
                        #         )
                        #         attachment.thumbnail = thumbnail_process
                        #         attachment.thumbnail_size = len(attachment.thumbnail)
                        #         # 提取缩略图尺寸
                        #         thumbnail_width, thumbnail_height = await self.get_jpeg_size_from_bytes(thumbnail_process, evt, debug)
                        #         if thumbnail_width:
                        #             attachment.thumbnail_width = thumbnail_width
                        #         if thumbnail_height:
                        #             attachment.thumbnail_height = thumbnail_height

                        #     except Exception as e:
                        #         if debug:
                        #             await evt.respond(f"[DEBUG] Error generating thumbnail: {e}")
                        
                        # if attachment.thumbnail is not None and attachment.thumbnail_height and attachment.thumbnail_height > 0:
                        #     attachment.thumbnail_uri = await self.client.upload_media(
                        #         data=attachment.thumbnail,
                        #         mime_type="image/jpeg",
                        #         filename=f"{splitext(file_info['filename'])[0]}-thumbnail.jpg",
                        #         size=attachment.thumbnail_size
                        #     )
                        #     if debug:
                        #         await evt.respond(f"[DEBUG] Thumbnail URI: {attachment.thumbnail_uri}")

                    except Exception as e:
                        if debug:
                            await evt.respond(f"[DEBUG] File upload failed: {str(e)}")
                        return
                else:
                    if debug:
                        await evt.respond(f"[DEBUG] Found attachment in database!")

                info = None
                message_type = None
                    
                if is_video:
                    info = VideoInfo(
                        mimetype=attachment.mimetype,
                        size=attachment.size,
                        width=attachment.width if attachment.width else None,
                        height=attachment.height if attachment.height else None,
                        duration=int(attachment.duration) if attachment.duration else None
                        # thumbnail_info=ThumbnailInfo(
                        #     width=attachment.thumbnail_width if attachment.thumbnail_width else None,
                        #     height=attachment.thumbnail_height if attachment.thumbnail_height else None,
                        #     mimetype="image/jpeg" if attachment.thumbnail else None,
                        #     size=attachment.thumbnail_size if attachment.thumbnail else None
                        # ) if attachment.thumbnail else None,
                        # thumbnail_url=attachment.thumbnail_uri if attachment.thumbnail_uri else None
                    )
                    message_type = MessageType.VIDEO

                elif is_audio:
                    info = AudioInfo(
                        mimetype=attachment.mimetype,
                        size=attachment.size,
                        duration=int(attachment.duration) if attachment.duration else None
                    )
                    message_type = MessageType.AUDIO

                elif is_image:
                    info = ImageInfo(
                        mimetype=attachment.mimetype,
                        size=attachment.size,
                        width=attachment.width if attachment.width else None,
                        height=attachment.height if attachment.height else None
                        # thumbnail_info=ThumbnailInfo(
                        #     width=attachment.thumbnail_width if attachment.thumbnail_width else None,
                        #     height=attachment.thumbnail_height if attachment.thumbnail_height else None,
                        #     mimetype="image/jpeg" if attachment.thumbnail else None,
                        #     size=attachment.thumbnail_size if attachment.thumbnail else None
                        # ) if attachment.thumbnail else None,
                        # thumbnail_url=attachment.thumbnail_uri if attachment.thumbnail_uri else None
                    )
                    message_type = MessageType.IMAGE

                else:
                    info = FileInfo(
                        mimetype=attachment.mimetype,
                        size=attachment.size
                        # thumbnail_info=ThumbnailInfo(
                        #     width=attachment.thumbnail_width if attachment.thumbnail_width else None,
                        #     height=attachment.thumbnail_height if attachment.thumbnail_height else None,
                        #     mimetype="image/jpeg" if attachment.thumbnail else None,
                        #     size=attachment.thumbnail_size if attachment.thumbnail else None
                        # ) if attachment.thumbnail else None,
                    )
                    message_type = MessageType.FILE

                if debug:
                    await evt.respond(f"[DEBUG] Sending file with info: {info}")

                try:
                    await self.client.send_file(
                        room_id=evt.room_id,
                        url=attachment.uri,
                        info=info,
                        file_name=file_info["filename"],
                        file_type=message_type,
                        relates_to=relates_to_content
                    )
                    await self.dbm.store_attachment(attachment)

                except Exception as e:
                    if debug:
                        await evt.respond(f"[DEBUG] File sending failed: {str(e)}")                  

        except aiohttp.ClientError as e:
            if debug:
                await evt.respond(f"[DEBUG] Network error while processing URL {group}: {str(e)}")
        except asyncio.TimeoutError:
            if debug:
                await evt.respond(f"[DEBUG] Timeout while processing URL {group}")
        except Exception as e:
            if debug:
                await evt.respond(f"[DEBUG] An error occurred while processing URL {group}: {str(e)}")

    @event.on(EventType.ROOM_MESSAGE)
    async def handle_message(self, evt: MessageEvent) -> None:
        enabled = await self.dbm.is_enabled_in_room(evt.room_id)
        debug = await self.dbm.is_debug_in_room(evt.room_id)
        if evt.sender != enabled and self.is_whitelisted(evt.sender):
            body = evt.content.body
             # body = evt.content.formatted_body if "formatted_body" in evt.content else None
            # if body is None:
            #     body = evt.content.body
            # else:
            #     body = html.unescape(body)
            m = set(re.findall(self.get_url_regex(), body))
            if debug:
                await evt.respond(f"[DEBUG] Found URL(s): {str(m)}")
            
            relates_to_content = None
            if evt.content.relates_to and evt.content.relates_to.rel_type == RelationType.THREAD:
                relates_to_content = RelatesTo(
                    rel_type=RelationType.THREAD,
                    event_id=EventID(evt.content.relates_to.event_id),
                    is_falling_back=True,
                    in_reply_to=InReplyTo(
                        event_id=EventID(evt.event_id)
                    )
                )
            
            for group in m:
                await self.process_url(group, evt, debug, relates_to_content)

            if debug:
                await evt.respond("[DEBUG] Finished processing all URLs in this message.")