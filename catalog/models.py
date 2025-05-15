from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
import uuid

db = SQLAlchemy()

class Movie(db.Model):
    __tablename__ = 'movies'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    genre = db.Column(db.String(100))
    rating = db.Column(db.Float)
    release_year = db.Column(db.String(4))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_recommended = db.Column(db.Boolean, default=False, nullable=False)

    def __repr__(self):
        return f'<Movie {self.title}>'

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'genre': self.genre,
            'rating': self.rating,
            'release_year': self.release_year,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        } 