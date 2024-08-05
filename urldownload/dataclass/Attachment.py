from asyncpg import Record
from attr import dataclass


@dataclass
class Attachment:
    sha512sum: str = ''
    uri: str = ''
    mimetype: str = ''
    size: int = 0
    thumbnail_uri: str = ''
    width: int = 0
    height: int = 0
    duration: float = 0.0
    thumbnail: bytes | None = None
    thumbnail_width: int = 0
    thumbnail_height: int = 0
    thumbnail_size: int = 0
    url: str = ''


    @classmethod
    def from_row(cls, row: Record | None):
        if not row:
            return None
        return cls(**row)