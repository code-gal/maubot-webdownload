from mautrix.types import RoomID
from mautrix.util.async_db import Database

from urldownload.dataclass.Attachment import Attachment


class DBManager:
    db: Database

    def __init__(self, db: Database) -> None:
        self.db = db

    async def join_room(self, room_id: RoomID) -> None:
        q = """
        INSERT INTO status (room_id, enabled, debug)
        VALUES ($1, $2, $3)
        """
        await self.db.execute(q, room_id, False, False)

    async def is_in_room(self, room_id: RoomID) -> bool:
        q = """
        SELECT enabled
        FROM status
        WHERE room_id = $1
        """
        rows = await self.db.fetch(q, room_id)

        return rows is not None and len(rows) > 0

    async def ensure_in_room(self, room_id: RoomID) -> None:
        if not await self.is_in_room(room_id):
            await self.join_room(room_id)

    async def is_enabled_in_room(self, room_id: RoomID) -> bool:
        q = """
        SELECT enabled
        FROM status
        WHERE room_id = $1
        """
        rows = await self.db.fetch(q, room_id)

        return rows[0]["enabled"] if rows is not None and len(rows) > 0 else False

    async def set_enabled_in_room(self, room_id: RoomID, enabled:bool=True) -> bool:
        await self.ensure_in_room(room_id)

        q = """
        UPDATE status
        SET enabled = $1
        WHERE room_id = $2
        """
        await self.db.execute(q, enabled, room_id)

    async def is_debug_in_room(self, room_id: RoomID) -> bool:
        q = """
        SELECT debug
        FROM status
        WHERE room_id = $1
        """
        rows = await self.db.fetch(q, room_id)

        return rows[0]["debug"] if rows is not None and len(rows) > 0 else False

    async def set_debug_in_room(self, room_id: RoomID, debug: bool = True) -> bool:
        await self.ensure_in_room(room_id)

        q = """
        UPDATE status
        SET debug = $1
        WHERE room_id = $2
        """
        await self.db.execute(q, debug, room_id)

    async def get_attachment(self, sha512sum: str) -> str:
        q = """
        SELECT
            sha512sum,
            uri,
            mimetype,
            size,
            thumbnail_uri,
            width,
            height,
            duration,
            thumbnail_width,
            thumbnail_height,
            thumbnail_size,
            url
        FROM attachment
        WHERE sha512sum = $1
        """

        rows = await self.db.fetch(q, sha512sum)

        if rows is None or len(rows) == 0:
            return None
        else:
            return Attachment.from_row(rows[0])

    async def store_attachment(self, attachment: Attachment):
        q = """
        INSERT INTO attachment (
            sha512sum,
            uri,
            mimetype,
            size,
            thumbnail_uri,
            width,
            height,
            duration,
            thumbnail_width,
            thumbnail_height,
            thumbnail_size,
            url
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """

        await self.db.execute(q,
                              attachment.sha512sum,
                              attachment.uri,
                              attachment.mimetype,
                              attachment.size,
                              attachment.thumbnail_uri,
                              attachment.width,
                              attachment.height,
                              attachment.duration,
                              attachment.thumbnail_width,
                              attachment.thumbnail_height,
                              attachment.thumbnail_size,
                              attachment.url
                              )