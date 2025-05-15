import os
import uuid
from datetime import datetime
import grpc
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, FloatField, SubmitField
from wtforms.validators import DataRequired, NumberRange
from dotenv import load_dotenv
from models import db, Movie
import recommendations_pb2
import recommendations_pb2_grpc

# Kafka imports
from confluent_kafka import Consumer, KafkaError, KafkaException
import json
import threading
import time # For sleep in consumer loop

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Create gRPC channel to recommendations service (still needed for preferences)
recommendations_host = os.getenv('RECOMMENDATIONS_HOST', 'localhost')
recommendations_channel = grpc.insecure_channel(f'{recommendations_host}:50051')
recommendations_stub = recommendations_pb2_grpc.RecommendationsStub(recommendations_channel)

# --- Kafka Consumer Setup ---
KAFKA_BROKER_CATALOG = os.getenv('KAFKA_BROKER', 'kafka:9092')
KAFKA_TOPIC_RECOMMENDATION_STATUS_CATALOG = 'film_recommendation_status_updates'
KAFKA_CONSUMER_GROUP_CATALOG = 'catalog_service_group'

def kafka_consumer_thread_function(app_context):
    app.logger.critical("!!! KAFKA CONSUMER THREAD FUNCTION ENTERED !!!")
    print("!!! KAFKA CONSUMER THREAD FUNCTION ENTERED !!!", flush=True)
    try:
        consumer_conf = {
            'bootstrap.servers': KAFKA_BROKER_CATALOG,
            'group.id': KAFKA_CONSUMER_GROUP_CATALOG,
            'auto.offset.reset': 'earliest',
            'socket.connection.setup.timeout.ms': 30000, # Increased timeout
            'reconnect.backoff.ms': 1000,               # Increased backoff
            'reconnect.backoff.max.ms': 10000,          # Increased max backoff
            # 'enable.auto.commit': False # If you want to manually commit offsets
            # For deeper debugging, you could add: 'debug': 'consumer,cgrp,topic,fetch,all'
        }
        consumer = Consumer(consumer_conf)
        app.logger.info("Kafka consumer object created.")
        print("Kafka consumer object created.", flush=True)

        consumer.subscribe([KAFKA_TOPIC_RECOMMENDATION_STATUS_CATALOG])
        app.logger.info(f"Kafka consumer subscribed to topic '{KAFKA_TOPIC_RECOMMENDATION_STATUS_CATALOG}' with group '{KAFKA_CONSUMER_GROUP_CATALOG}'")
        print(f"Kafka consumer subscribed to topic '{KAFKA_TOPIC_RECOMMENDATION_STATUS_CATALOG}'", flush=True)
        
        app.logger.info(f"Kafka consumer thread and session for DB operations initialized. Entering poll loop...")
        print("Kafka consumer entering poll loop...", flush=True)

        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                time.sleep(1)
                continue
            
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    app.logger.info(f'%% {msg.topic()} [{msg.partition()}] reached end at offset {msg.offset()}\n')
                elif msg.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                    app.logger.warning(f"Kafka consumer warning: Topic '{KAFKA_TOPIC_RECOMMENDATION_STATUS_CATALOG}' not available yet. Retrying... Error: {msg.error()}")
                    time.sleep(5) # Wait a bit longer before retrying poll for this specific error
                    continue # Continue to the next poll attempt
                else:
                    app.logger.error(f"Kafka consumer error: {msg.error()}. Stopping consumer thread.")
                    break # Break on other persistent errors
            else:
                app.logger.info(f"Kafka consumer: Raw message received from topic {msg.topic()}: {msg.value()}")
                decoded_message = msg.value().decode('utf-8')
                app.logger.info(f"Kafka consumer: Decoded message: {decoded_message}")
                try:
                    message_data = json.loads(decoded_message)
                    app.logger.info(f"Kafka consumer: Parsed message data: {message_data}")
                    film_id = message_data.get('film_id')
                    is_recommended_status = message_data.get('is_recommended')

                    app.logger.info(f"Kafka consumer: Extracted film_id: {film_id}, is_recommended: {is_recommended_status}")

                    if film_id and isinstance(is_recommended_status, bool):
                        app.logger.info(f"Kafka consumer: Attempting to acquire app context for DB operation for film_id: {film_id}")
                        with app_context: # Existing: app.app_context() was passed as app_context
                            app.logger.info(f"Kafka consumer: App context acquired. Querying for movie_id: {film_id}")
                            movie_to_update = Movie.query.get(film_id)
                            if movie_to_update:
                                app.logger.info(f"Kafka consumer: Found movie: {movie_to_update.title}. Current is_recommended: {movie_to_update.is_recommended}. Attempting to set to: {is_recommended_status}")
                                movie_to_update.is_recommended = is_recommended_status
                                db.session.commit()
                                app.logger.info(f"Kafka consumer: Successfully committed update for movie ID {film_id}. New is_recommended: {movie_to_update.is_recommended}")
                            else:
                                app.logger.warning(f"Kafka consumer: Movie ID {film_id} not found in catalog database.")
                    else:
                        app.logger.warning(f"Kafka consumer: Invalid or incomplete message data from Kafka: {message_data}")
                except json.JSONDecodeError:
                    app.logger.error(f"Failed to decode JSON from Kafka message: {decoded_message}")
                except Exception as e:
                    app.logger.error(f"Error processing Kafka message: {e}")
            time.sleep(0.1)
    except Exception as e: # Broader catch for initialization phase
        app.logger.error(f"CRITICAL ERROR IN KAFKA CONSUMER INIT PHASE: {e}", exc_info=True)
        print(f"CRITICAL ERROR IN KAFKA CONSUMER INIT PHASE: {e}", flush=True)
    finally:
        consumer.close()
        app.logger.info("Kafka consumer closed.")

# Start Kafka consumer in a background thread
# We need the app context to be available in the thread
kafka_thread = threading.Thread(target=kafka_consumer_thread_function, args=(app.app_context(),), daemon=True)
kafka_thread.start()
app.logger.info("Kafka consumer background thread started.")
# --- End Kafka Consumer Setup ---


def notify_recommendations_service(movie):
    """Notify recommendations service about a new or updated movie (via gRPC)"""
    try:
        keywords = set()
        if movie.title: keywords.update(movie.title.lower().split())
        if movie.description: keywords.update(movie.description.lower().split())
        if movie.genre: keywords.update(movie.genre.lower().split())

        # Prepare gRPC request (FilmUpdate) for recommendations service
        film_update_request = recommendations_pb2.FilmUpdate(
            film_id=movie.id,
            title=movie.title,
            description=movie.description if movie.description else "",
            genre=movie.genre if movie.genre else "",
            rating=movie.rating if movie.rating is not None else 0.0,
            release_year=movie.release_year if movie.release_year else "",
            keywords=list(keywords)
        )
        recommendations_stub.ProcessFilmUpdate(film_update_request)
        app.logger.info(f"Notified recommendations service (gRPC) about movie: {movie.title}")
    except grpc.RpcError as e:
        app.logger.error(f"Failed to notify recommendations service (gRPC): {str(e)}")

class MovieForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired()])
    description = TextAreaField('Description')
    genre = StringField('Genre')
    rating = FloatField('Rating', validators=[NumberRange(min=0, max=10)])
    release_year = StringField('Release Year')
    submit = SubmitField('Submit')

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    genre_filter = request.args.get('genre')
    
    query = Movie.query
    if genre_filter:
        query = query.filter(Movie.genre.ilike(f'%{genre_filter}%')) # Case-insensitive like
    
    pagination = query.order_by(Movie.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template('index.html', pagination=pagination, current_genre=genre_filter)

@app.route('/movie/new', methods=['GET', 'POST']) 
def new_movie():
    form = MovieForm()
    if form.validate_on_submit():
        new_movie_obj = Movie(
            # id is generated by default in model
            title=form.title.data,
            description=form.description.data,
            genre=form.genre.data,
            rating=form.rating.data,
            release_year=form.release_year.data
        )
        db.session.add(new_movie_obj)
        db.session.commit()
        notify_recommendations_service(new_movie_obj) # Notify recommendations via gRPC
        flash('Movie added successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('movie_form.html', form=form, title='New Movie')

@app.route('/movie/<string:id>/edit', methods=['GET', 'POST'])
def edit_movie(id):
    movie_obj = Movie.query.get_or_404(id)
    form = MovieForm(obj=movie_obj)
    if form.validate_on_submit():
        movie_obj.title = form.title.data
        movie_obj.description = form.description.data
        movie_obj.genre = form.genre.data
        movie_obj.rating = form.rating.data
        movie_obj.release_year = form.release_year.data
        db.session.commit()
        notify_recommendations_service(movie_obj) # Notify recommendations via gRPC
        flash('Movie updated successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('movie_form.html', form=form, title='Edit Movie', movie=movie_obj)

@app.route('/movie/<string:id>/delete', methods=['POST'])
def delete_movie(id):
    movie_obj = Movie.query.get_or_404(id)
    db.session.delete(movie_obj)
    db.session.commit()
    # Optionally, send a Kafka message or gRPC call if recommendations need to be updated on delete
    flash('Movie deleted successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/movie/<string:id>')
def view_movie(id):
    movie_obj = Movie.query.get_or_404(id)
    return render_template('movie_detail.html', movie=movie_obj)

@app.route('/preferences')
def preferences():
    try:
        response = recommendations_stub.GetPreferences(recommendations_pb2.GetPreferencesRequest(user_id=1))
        return render_template('preferences.html', preferences=response.preferences)
    except grpc.RpcError as e:
        flash(f'Error fetching preferences: {str(e)}', 'error')
        return render_template('preferences.html', preferences=[])

@app.route('/preferences/add', methods=['POST'])
def add_preference():
    keyword = request.form.get('keyword')
    if not keyword:
        flash('Keyword is required', 'error'); return redirect(url_for('preferences'))
    try:
        recommendations_stub.CreatePreference(
            recommendations_pb2.CreatePreferenceRequest(user_id=1, keyword=keyword)
        )
        flash('Preference added successfully', 'success')
    except grpc.RpcError as e: flash(f'Error adding preference: {str(e)}', 'error')
    return redirect(url_for('preferences'))

@app.route('/preferences/delete/<keyword>', methods=['POST'])
def delete_preference(keyword):
    try:
        response = recommendations_stub.DeletePreference(
            recommendations_pb2.DeletePreferenceRequest(user_id=1, keyword=keyword)
        )
        if response.success: flash('Preference deleted successfully', 'success')
        else: flash('Failed to delete preference', 'error')
    except grpc.RpcError as e: flash(f'Error deleting preference: {str(e)}', 'error')
    return redirect(url_for('preferences'))

@app.route('/recommendations')
def recommendations(): # This route now uses the local DB flag
    try:
        app.logger.info("Fetching recommendations from local catalog database (is_recommended=True)")
        recommended_movies_from_db = Movie.query.filter_by(is_recommended=True).order_by(Movie.updated_at.desc()).all()
        
        if not recommended_movies_from_db:
            app.logger.info("No movies marked as recommended found in catalog database.")
            flash('No recommendations available yet. Movies get marked as recommended based on your preferences over time!', 'info')
        
        app.logger.info(f"Found {len(recommended_movies_from_db)} recommended movies in catalog DB.")
        return render_template('recommendations.html', movies=recommended_movies_from_db)
            
    except Exception as e: # Catch any other exception during DB query or rendering
        app.logger.error(f"Error displaying recommendations from catalog DB: {str(e)}")
        flash('Could not load recommendations at this time.', 'error')
        return render_template('recommendations.html', movies=[])

if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Ensure tables are created
    app.run(host='0.0.0.0', port=5000, debug=True) # debug=True for development
