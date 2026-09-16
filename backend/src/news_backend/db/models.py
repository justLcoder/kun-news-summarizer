"""Source-independent article persistence."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_articles"),
        UniqueConstraint("source_url", name="uq_articles_source_url"),
        CheckConstraint("title ~ '[^[:space:]]'", name="ck_articles_title_nonblank"),
        CheckConstraint("content ~ '[^[:space:]]'", name="ck_articles_content_nonblank"),
        {"comment": "Successfully retrieved source articles"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @validates("published_at")
    def validate_published_at(self, key: str, value: datetime) -> datetime:
        if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
            raise ValueError("published_at must be a timezone-aware datetime")
        return value


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (
        PrimaryKeyConstraint("article_id", name="pk_summaries"),
        CheckConstraint("content ~ '[^[:space:]]'", name="ck_summaries_content_nonblank"),
    )

    article_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("articles.id", name="fk_summaries_article_id", ondelete="CASCADE"), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @validates("generated_at")
    def validate_generated_at(self, key: str, value: datetime) -> datetime:
        if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
            raise ValueError("generated_at must be a timezone-aware datetime")
        return value


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stories"),
        CheckConstraint(
            "first_published_at <= last_published_at",
            name="ck_stories_publication_bounds",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    first_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @validates("first_published_at", "last_published_at", "updated_at")
    def validate_timestamps(self, key: str, value: datetime) -> datetime:
        if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
            raise ValueError(f"{key} must be a timezone-aware datetime")
        return value


class StoryArticle(Base):
    __tablename__ = "story_articles"
    __table_args__ = (PrimaryKeyConstraint("article_id", name="pk_story_articles"),)

    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("articles.id", name="fk_story_articles_article_id", ondelete="CASCADE"),
        primary_key=True,
    )
    story_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("stories.id", name="fk_story_articles_story_id", ondelete="CASCADE"),
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index(
    "ix_stories_last_published_at_id",
    Story.last_published_at.desc(),
    Story.id.desc(),
)
Index("ix_story_articles_story_id", StoryArticle.story_id)
