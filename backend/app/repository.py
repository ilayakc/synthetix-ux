from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Repository'lerin uzerine kurulacagi temel sinif.

    Somut repository'ler (ornegin `OrganizationRepository`) bu sinifi
    genisleterek kendi sorgu metodlarini ekler. CRUD/is mantigi bu
    asamanin kapsami disindadir; burada yalnizca session baglama
    yapisi kurulur.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
