from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Text, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()

class Preference(Base):
    __tablename__ = 'prefs'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    keyword = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Removed: Relationship with recommendations
    # recommendations = relationship("Recommendation", back_populates="preference")

class Recommendation(Base):
    __tablename__ = 'recommendations'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    film_id = Column(String(36), nullable=False)  # UUID as string
    title = Column(String(255), nullable=False)
    description = Column(Text)
    genre = Column(String(100))
    rating = Column(Float)
    release_year = Column(String(4))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Removed: Relationship with preference
    # preference = relationship("Preference", back_populates="recommendations")

# Database connection
DATABASE_URL = os.getenv('DATABASE_URL', 'mysql://soaproject:soaproject@localhost:3306/recommendations')
engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine) 