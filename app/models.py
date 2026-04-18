from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    authors: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str | None] = mapped_column(String(256), nullable=True)
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)

    tweets: Mapped[list[Tweet]] = relationship("Tweet", back_populates="paper")

    @property
    def tweet_count(self) -> int:
        return len(self.tweets)


class Tweet(Base):
    __tablename__ = "tweets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    twitter_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    author_name: Mapped[str] = mapped_column(String(256), default="")
    author_handle: Mapped[str] = mapped_column(String(256), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1024), default="")
    tweeted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    paper_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("papers.id"), nullable=False
    )

    paper: Mapped[Paper] = relationship("Paper", back_populates="tweets")
