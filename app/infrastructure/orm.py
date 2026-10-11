"""SQLAlchemy ORM tables: `files` and `features`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class FileORM(Base):
    """ORM model for uploaded files."""

    __tablename__ = "files"

    id = Column(Text, primary_key=True)  # UUID string
    filename = Column(Text, nullable=False)
    file_type = Column(Text, nullable=False)  # "shapefile_zip" or "kml"
    stored_path = Column(Text, nullable=False)
    status = Column(Text, nullable=False, index=True)  # PENDING, PROCESSING, COMPLETED, FAILED
    crs = Column(Text, nullable=True)
    feature_count = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationship to features
    features = relationship("FeatureORM", back_populates="file", cascade="all, delete-orphan")


class FeatureORM(Base):
    """ORM model for per-feature measurements."""

    __tablename__ = "features"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Text, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_index = Column(Integer, nullable=False)
    layer = Column(Text, nullable=False)
    geometry_type = Column(Text, nullable=True, index=True)
    geometry = Column(JSON, nullable=True)  # GeoJSON in source CRS
    properties = Column(JSON, nullable=False)
    area_m2 = Column(Float, nullable=True)
    length_m = Column(Float, nullable=True)
    geodesic_area_m2 = Column(Float, nullable=True)
    geodesic_length_m = Column(Float, nullable=True)
    measurement_crs = Column(Text, nullable=True)
    note = Column(Text, nullable=True)

    # Relationship to file
    file = relationship("FileORM", back_populates="features")

    # Unique constraint on (file_id, feature_index)
    __table_args__ = (
        UniqueConstraint("file_id", "feature_index", name="uq_file_feature_index"),
        Index("ix_features_file_id_geometry_type", "file_id", "geometry_type"),
    )
