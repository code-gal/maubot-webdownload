from mautrix.util.async_db import UpgradeTable, Scheme, Connection

upgrade_table = UpgradeTable()

@upgrade_table.register(description="Initial revision")
async def upgrade_v1(conn: Connection, scheme: Scheme) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS status (
            room_id TEXT PRIMARY KEY,
            enabled BOOLEAN DEFAULT false,
            debug BOOLEAN DEFAULT false
        )"""
    )


@upgrade_table.register(description="Keep track of known attachments")
async def upgrade_v2(conn: Connection) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS attachment (
            sha512sum TEXT PRIMARY KEY,
            uri TEXT NOT NULL,
            mimetype TEXT NOT NULL,
            size BIGINT NOT NULL,
            thumbnail_uri TEXT,
            width INTEGER,
            height INTEGER,
            duration NUMERIC,
            thumbnail_width INTEGER,
            thumbnail_height INTEGER,
            thumbnail_size INTEGER
        )"""
    )


@upgrade_table.register(description="Keep track of URL for attachment")
async def upgrade_v3(conn: Connection) -> None:
    await conn.execute(
    """
        ALTER TABLE attachment
        ADD COLUMN url TEXT NOT NULL
    """
    )