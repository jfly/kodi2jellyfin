#!/usr/bin/env python

import csv
import logging
import sqlite3
import argparse
import datetime as dt
from pathlib import Path
from typing import Generator
from contextlib import closing
from dataclasses import dataclass
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# https://docs.python.org/3/library/sqlite3.html#adapter-and-converter-recipes
def adapt_datetime_epoch(val):
    """Adapt datetime.datetime to Unix timestamp."""
    return int(val.timestamp())
sqlite3.register_adapter(dt.datetime, adapt_datetime_epoch)

@dataclass
class KodiWatched:
    folder: str
    file_name: str
    last_played:  dt.datetime | None
    play_count: int

    @property
    def path(self):
        return self.folder + self.file_name

    @classmethod
    def parse(cls, kodi_tsv: Path) -> Generator['KodiWatched', None, None]:
        with kodi_tsv.open(newline='') as fd:
            reader = csv.DictReader(fd, delimiter="\t")
            for row in reader:
                yield cls(
                    folder=row['strPath'],
                    file_name=row['strFileName'],
                    last_played=dt.datetime.fromisoformat(row['lastPlayed']),
                    play_count=int(row['playCount']),
                )

@dataclass
class JellyfinUser:
    username: str
    internal_id: str

@dataclass
class UserData:
    key: str
    user_id: str
    played: bool
    play_count: int
    last_played_date: dt.datetime
    is_favorite: bool
    playback_position_ticks: int

class JellyfinData:
    def __init__(self, jellyfin_con: sqlite3.Connection, library_con: sqlite3.Connection):
        jellyfin_con.row_factory = JellyfinData._dict_factory
        library_con.row_factory = JellyfinData._dict_factory

        self._jellyfin = jellyfin_con
        self._library = library_con

    @staticmethod
    def _dict_factory(cursor, row):
        fields = [column[0] for column in cursor.description]
        return {key: value for key, value in zip(fields, row)}

    def get_user_by_name(self, username: str) -> JellyfinUser:
        row = self._jellyfin.execute(
            "SELECT InternalId, Username FROM Users WHERE Username = :username",
            {"username": username}
        ).fetchone()

        return JellyfinUser(
            internal_id=row['InternalId'],
            username=row['Username'],
        )

    def get_user_data_key_for_path(self, path: str) -> str | None:
        row = self._library.execute(
            "SELECT UserDataKey FROM TypedBaseItems WHERE Path = :path",
            {"path": path}
        ).fetchone()

        if row is None:
            return None

        return row['UserDataKey']

    def upsert_user_data(
        self,
        key: str,
        user_id: str,
        played: bool,
        play_count: int,
        last_played_date: dt.datetime,
    ):
        self._library.execute(
            """
            REPLACE INTO UserDatas (key, userId, played, playCount, lastPlayedDate, isFavorite, playbackPositionTicks)
            VALUES (:key, :user_id, :played, :play_count, :last_played_date, :is_favorite, :playback_position_ticks)
            """,
            {
                "key": key,
                "user_id": user_id,
                "played": played,
                "play_count": play_count,
                "last_played_date": last_played_date,
                "is_favorite": False,
                "playback_position_ticks": 0,
            },
        )

    def get_user_data(self, key: str) -> UserData|None:
        row = self._library.execute(
            "SELECT * FROM UserDatas WHERE key = :key",
            {"key": key}
        ).fetchone()

        if row is None:
            return None

        return UserData(
            key=row['key'],
            user_id=row['userId'],
            played=row['played'],
            play_count=row['playCount'],
            last_played_date=row['lastPlayedDate'],
            is_favorite=row['isFavorite'],
            playback_position_ticks=row['playbackPositionTicks'],
        )


    @classmethod
    @contextmanager
    def open(cls, jellyfin_data_dir: Path):
        with (
            # https://docs.python.org/3/library/sqlite3.html#sqlite3-connection-context-manager
            # > Note: The context manager neither implicitly opens a new transaction nor closes
            # > the connection. If you need a closing context manager, consider using contextlib.closing().
            closing(sqlite3.connect(jellyfin_data_dir / "jellyfin.db")) as jellyfin_con,
            closing(sqlite3.connect(jellyfin_data_dir / "library.db")) as library_con,
        ):
            with jellyfin_con, library_con:
                yield cls(
                    jellyfin_con=jellyfin_con,
                    library_con=library_con,
                )
                jellyfin_con.commit()
                library_con.commit()


def kodi2jellyfin(kodi_tsv: Path, jellyfin_data_dir: Path, jellyfin_username: str):
    with JellyfinData.open(jellyfin_data_dir) as jellyfin_data:
        user = jellyfin_data.get_user_by_name(jellyfin_username)
        kodi_watched_missing_from_jellyfin = []

        for kodi_watched in KodiWatched.parse(kodi_tsv):
            skip = (
                kodi_watched.path == "/"
                or kodi_watched.folder.startswith("plugin://")
            )
            if skip:
                logging.debug(f"Skipping {kodi_watched}")
                continue

            user_data_key = jellyfin_data.get_user_data_key_for_path(kodi_watched.path)

            if user_data_key is None:
                kodi_watched_missing_from_jellyfin.append(kodi_watched)
                continue

            assert kodi_watched.last_played is not None
            jellyfin_data.upsert_user_data(
                key=user_data_key,
                user_id=user.internal_id,
                played=kodi_watched.play_count > 0,
                play_count=kodi_watched.play_count,
                last_played_date=kodi_watched.last_played,
            )

        if len(kodi_watched_missing_from_jellyfin) > 0:
            warning = "I ran into some files that are marked as watched in Kodi, but don't exist over in Jellyfin"
            for kodi_watched in kodi_watched_missing_from_jellyfin:
                warning += "\n" + kodi_watched.path

            logging.warning(warning)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kodi_tsv", help="A dump of Kodi's watch status. See README.md for the exact format")
    parser.add_argument("jellyfin_data_dir", help="A path to a Jellyfin data directory. Don't be crazy, make a copy!")
    parser.add_argument("--jellyfin-username", required=True, help="The Jellyfin user whose watched status we should update.")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    kodi2jellyfin(
        Path(args.kodi_tsv),
        Path(args.jellyfin_data_dir),
        jellyfin_username=args.jellyfin_username,
    )

if __name__ == "__main__":
    main()
