import grpc
import random
import logging
from concurrent import futures
from sqlalchemy.orm import sessionmaker
from confluent_kafka import Producer # Kafka
import json # Kafka
import os # Added for os.getenv

# Protobuf imports
from recommendations_pb2 import (
    MovieCategory,
    MovieRecommendation,
    RecommendationResponse, # Corrected from RecommendationResponse
    Preference as GrpcPreference, # Alias to avoid clash with model
    CreatePreferenceRequest,
    GetPreferencesRequest,
    GetPreferencesResponse,
    DeletePreferenceRequest,
    DeletePreferenceResponse,
    FilmUpdate,
    FilmUpdateResponse
)
import recommendations_pb2_grpc

# Model imports
from models import Preference as PreferenceModel, Recommendation as RecommendationModel, engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create session factory
Session = sessionmaker(bind=engine)

# Kafka Producer Configuration
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092') # Use os.getenv
KAFKA_TOPIC_RECOMMENDATION_STATUS = 'film_recommendation_status_updates'

def get_kafka_producer():
    try:
        producer_conf = {
            'bootstrap.servers': KAFKA_BROKER,
            'socket.connection.setup.timeout.ms': 30000, # Increased timeout
            'reconnect.backoff.ms': 1000,               # Increased backoff
            'reconnect.backoff.max.ms': 10000           # Increased max backoff
            # For deeper debugging, you could add: 'debug': 'broker,topic,msg,all'
        }
        producer = Producer(producer_conf)
        logger.info(f"Kafka Producer initialized for brokers: {KAFKA_BROKER} with increased timeouts.")
        return producer
    except Exception as e:
        logger.error(f"Failed to initialize Kafka Producer: {e}")
        return None

kafka_producer = get_kafka_producer()

def delivery_report(err, msg):
    """ Called once for each message produced to indicate delivery result. """
    if err is not None:
        logger.error(f'Message delivery failed: {err}')
    else:
        logger.info(f'Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}')

class RecommendationService(recommendations_pb2_grpc.RecommendationsServicer):
    def __init__(self):
        self.session = Session() # Session per instance, or manage per request? Let's try per request for now.
        self.kafka_producer = kafka_producer # Use the global producer

    # Corrected GetPreferences, was missing in previous full file view but likely existed
    def GetPreferences(self, request, context):
        session = Session()
        try:
            preferences = session.query(PreferenceModel).filter(
                PreferenceModel.user_id == request.user_id
            ).all()
            
            grpc_preferences = []
            for p in preferences:
                grpc_preferences.append(GrpcPreference(user_id=p.user_id, keyword=p.keyword))

            return GetPreferencesResponse(preferences=grpc_preferences)
        except Exception as e:
            logger.error(f"Error in GetPreferences: {e}")
            context.abort(grpc.StatusCode.INTERNAL, "Error fetching preferences")
        finally:
            session.close()

    def CreatePreference(self, request, context):
        session = Session()
        try:
            preference = PreferenceModel(
                user_id=request.user_id,
                keyword=request.keyword
            )
            session.add(preference)
            session.commit()
            
            # Return the created preference in gRPC format
            return GrpcPreference( 
                user_id=preference.user_id,
                keyword=preference.keyword
            )
        except Exception as e:
            logger.error(f"Error in CreatePreference: {e}")
            session.rollback()
            context.abort(grpc.StatusCode.INTERNAL, "Error creating preference")
        finally:
            session.close()

    def DeletePreference(self, request, context):
        session = Session()
        try:
            preference = session.query(PreferenceModel).filter(
                PreferenceModel.user_id == request.user_id,
                PreferenceModel.keyword == request.keyword
            ).first()
            
            if preference:
                session.delete(preference)
                session.commit()
                return DeletePreferenceResponse(success=True)
            return DeletePreferenceResponse(success=False)
        except Exception as e:
            logger.error(f"Error in DeletePreference: {e}")
            session.rollback()
            context.abort(grpc.StatusCode.INTERNAL, "Error deleting preference")
        finally:
            session.close()

    def ProcessFilmUpdate(self, request: FilmUpdate, context): # Use the lowercase one we've been working with
        """Process film updates from catalog service and publish recommendation status to Kafka."""
        session = Session()
        recommendations_created_count = 0
        try:
            logger.info(f"Processing film update for movie: {request.title} (ID: {request.film_id})")
            
            # Keywords from the film update request (already processed by catalog)
            film_keywords = set(k.lower() for k in request.keywords)
            logger.info(f"Film keywords for matching: {film_keywords}")
            
            user_preferences = session.query(PreferenceModel).all()
            logger.info(f"Found {len(user_preferences)} total preferences in DB.")
            
            films_to_mark_recommended = set()

            for pref in user_preferences:
                if pref.keyword.lower() in film_keywords:
                    logger.info(f"Match found! User {pref.user_id} preference '{pref.keyword}' matches film '{request.title}'.")
                    # Check if recommendation already exists to avoid duplicates
                    existing_recommendation = session.query(RecommendationModel).filter_by(
                        user_id=pref.user_id,
                        film_id=request.film_id
                    ).first()

                    if not existing_recommendation:
                        recommendation = RecommendationModel(
                            user_id=pref.user_id,
                            film_id=request.film_id,
                            title=request.title,
                            description=request.description,
                            genre=request.genre,
                            rating=request.rating if request.rating else 0.0, # Handle if rating is not provided
                            release_year=request.release_year if request.release_year else ""
                        )
                        session.add(recommendation)
                        recommendations_created_count += 1
                        films_to_mark_recommended.add(request.film_id)
                        logger.info(f"Created new recommendation entry for User ID {pref.user_id}, Film ID {request.film_id}.")
                    else:
                        logger.info(f"Recommendation already exists for User ID {pref.user_id}, Film ID {request.film_id}. Checking if it needs Kafka update.")
                        # Even if it exists, we might want to re-notify Kafka if the logic changes,
                        # or if the catalog somehow missed the previous message.
                        films_to_mark_recommended.add(request.film_id)


            if recommendations_created_count > 0:
                session.commit()
                logger.info(f"Successfully committed {recommendations_created_count} new recommendations to DB.")
            else:
                logger.info("No new recommendations created in DB for this film update.")
            
            # Publish to Kafka for each film that triggered a recommendation (new or existing)
            if self.kafka_producer:
                for film_id_to_notify in films_to_mark_recommended:
                    message_payload = {
                        'film_id': film_id_to_notify,
                        'is_recommended': True # Signal to catalog to mark this film
                    }
                    message_json = json.dumps(message_payload).encode('utf-8')
                    self.kafka_producer.produce(
                        KAFKA_TOPIC_RECOMMENDATION_STATUS,
                        value=message_json,
                        key=film_id_to_notify, # Use film_id as key for partitioning
                        callback=delivery_report
                    )
                    logger.info(f"Produced message to Kafka topic '{KAFKA_TOPIC_RECOMMENDATION_STATUS}': {message_payload}")
                
                # Wait for any outstanding messages to be delivered and delivery reports to be received.
                # Important: In a high-throughput app, you might not want to flush on every request.
                # Consider batching or periodic flushing.
                self.kafka_producer.flush(timeout=5) # Timeout in seconds
            else:
                logger.error("Kafka producer not available. Cannot send recommendation status update.")

            return FilmUpdateResponse(success=True)
            
        except Exception as e:
            logger.error(f"Error in ProcessFilmUpdate: {str(e)}")
            session.rollback()
            # context.abort(grpc.StatusCode.INTERNAL, str(e)) # This was there
            # For gRPC, it's better to set details and code on context
            context.set_details(f"Error processing film update: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return FilmUpdateResponse(success=False) # Ensure we always return a response
        finally:
            session.close()

    def GetRecommendations(self, request, context): # Use the lowercase one
        """Get movie recommendations for a user."""
        session = Session()
        try:
            user_id_int = int(request.user_id) # Ensure user_id is int for query
            recommendations_from_db = session.query(RecommendationModel).filter(
                RecommendationModel.user_id == user_id_int
            ).limit(request.max_results).all()
            
            logger.info(f"Found {len(recommendations_from_db)} total recommendations for user {user_id_int} from DB.")
            
            movie_recommendations_grpc = []
            for rec in recommendations_from_db:
                logger.info(f"Processing DB recommendation - Film ID: {rec.film_id}, Title: {rec.title}, User ID: {rec.user_id}")
                movie_recommendations_grpc.append(
                    MovieRecommendation(
                        id=rec.film_id,
                        title=rec.title,
                        description=rec.description if rec.description else "",
                        genre=rec.genre if rec.genre else "",
                        rating=rec.rating if rec.rating is not None else 0.0,
                        release_year=rec.release_year if rec.release_year else ""
                    )
                )
            
            logger.info(f"Successfully converted {len(movie_recommendations_grpc)} recommendations to gRPC format.")
            return RecommendationResponse(recommendations=movie_recommendations_grpc)
            
        except ValueError:
            logger.error(f"Invalid user_id format: {request.user_id}")
            context.set_details("Invalid user_id format. Must be an integer.")
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            return RecommendationResponse()
        except Exception as e:
            logger.error(f"Error in GetRecommendations: {str(e)}")
            context.set_details(f"Error getting recommendations: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return RecommendationResponse()
        finally:
            session.close()

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    recommendations_pb2_grpc.add_RecommendationsServicer_to_server(
        RecommendationService(), server
    )
    server.add_insecure_port("[::]:50051")
    logger.info("Recommendation gRPC server starting on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == "__main__":
    logger.info("Starting recommendation service...")
    serve()
