# Microservices Project: Movie Catalog and Recommendations

## 1. Overview

This project implements a microservices architecture for a movie catalog and recommendation system. It features two primary services: the **Catalog Service** and the **Recommendations Service**. These services interact through gRPC for synchronous operations and utilize Kafka for asynchronous, event-driven communication. The entire system is containerized using Docker and orchestrated with Docker Compose.

## 2. System Architecture

The system comprises the following key components:

*   **Catalog Service**: A Flask-based web application responsible for managing the movie inventory, handling user interactions (browsing movies, managing preferences via UI), and displaying recommendations.
*   **Recommendations Service**: A gRPC service responsible for managing user preferences, processing film updates from the Catalog Service to generate potential recommendations, and storing these recommendations.
*   **MySQL Databases**: Two separate logical databases (`movies` for Catalog, `recommendations` for Recommendations) hosted within a single MySQL Docker container. Each service interacts only with its dedicated database.
*   **gRPC**: Used for direct, synchronous communication between the Catalog and Recommendations services for specific request/response actions (e.g., preference management, film data synchronization).
*   **Apache Kafka**: Used for asynchronous, event-driven communication. Specifically, the Recommendations Service publishes events when a film is recommended, and the Catalog Service consumes these events to update its local data.
*   **Zookeeper**: Required by Kafka for cluster coordination.
*   **Docker & Docker Compose**: For containerizing each component and managing the multi-container application.

## 3. Services Detailed

### 3.1. Catalog Service

*   **Technology**: Python, Flask, SQLAlchemy, `confluent-kafka` (Python client for Kafka).
*   **Port**: `5000` (HTTP).
*   **Database**: MySQL, `movies` database.
    *   `Movie` table: Stores movie details (`id`, `title`, `description`, `genre`, `rating`, `release_year`, `created_at`, `updated_at`) and a boolean flag `is_recommended`.
*   **Responsibilities**:
    *   Provides a web UI for users to browse movies, add new movies, edit existing movies, and delete movies.
    *   Provides a UI for users to manage their keyword-based preferences (add/delete).
    *   Displays a list of recommended movies.
*   **Communication**:
    *   **Outbound gRPC**:
        *   Calls the Recommendations Service to create, retrieve, and delete user preferences.
        *   Sends a `FilmUpdate` gRPC message to the Recommendations Service whenever a movie is added or updated in the catalog. This message includes full film details and extracted keywords.
    *   **Kafka Consumer**:
        *   Subscribes to the `film_recommendation_status_updates` Kafka topic.
        *   When a message is received (e.g., `{'film_id': 'uuid', 'is_recommended': True}`), it updates the `is_recommended` flag for the corresponding movie in its local `movies` database.
        *   This consumer runs in a background thread within the Flask application.

### 3.2. Recommendations Service

*   **Technology**: Python, gRPC, SQLAlchemy, `confluent-kafka`.
*   **Port**: `50051` (gRPC).
*   **Database**: MySQL, `recommendations` database.
    *   `Preference` table: Stores user preferences (`id`, `user_id`, `keyword`, `created_at`).
    *   `Recommendation` table: Stores detailed information about films that have been identified as recommendations for users (`id`, `user_id`, `film_id`, `title`, `description`, `genre`, `rating`, `release_year`, `created_at`).
*   **Responsibilities**:
    *   Manages CRUD operations for user preferences via gRPC endpoints.
    *   Receives `FilmUpdate` messages from the Catalog Service.
    *   Processes these film updates by comparing film keywords (from title, description, genre) against stored user preferences.
    *   If a match is found, it creates (or verifies existence of) a comprehensive recommendation entry in its local `Recommendation` table.
*   **Communication**:
    *   **Inbound gRPC**:
        *   Exposes gRPC methods for `CreatePreference`, `GetPreferences`, `DeletePreference`.
        *   Exposes a `ProcessFilmUpdate` gRPC method to receive film data from the Catalog Service.
    *   **Kafka Producer**:
        *   When `ProcessFilmUpdate` identifies a film as a potential recommendation for a user (based on preference matching), it publishes a message to the `film_recommendation_status_updates` Kafka topic.
        *   The message payload is typically `{'film_id': 'uuid-of-the-film', 'is_recommended': True}`.

## 4. Databases

Two logically separate databases are used, although they reside in the same MySQL server container:

1.  **`movies` (for Catalog Service)**:
    *   `movies` table:
        *   `id` (String UUID, Primary Key)
        *   `title` (String)
        *   `description` (Text)
        *   `genre` (String)
        *   `rating` (Float)
        *   `release_year` (String)
        *   `created_at` (DateTime)
        *   `updated_at` (DateTime)
        *   `is_recommended` (Boolean, default: False) - *This flag is updated by the Kafka consumer.*

2.  **`recommendations` (for Recommendations Service)**:
    *   `prefs` table (renamed from `preferences`):
        *   `id` (Integer, Primary Key, Auto-increment)
        *   `user_id` (Integer) - Hardcoded to `1` in current implementation.
        *   `keyword` (String)
        *   `created_at` (DateTime)
    *   `recommendations` table:
        *   `id` (Integer, Primary Key, Auto-increment)
        *   `user_id` (Integer)
        *   `film_id` (String UUID) - Foreign key conceptually linking to `movies.id` in catalog.
        *   `title` (String)
        *   `description` (Text)
        *   `genre` (String)
        *   `rating` (Float)
        *   `release_year` (String)
        *   `created_at` (DateTime)

SQL initialization scripts (`init.sql` for catalog, `init_recommendations.sql` for recommendations) are used to create these databases and tables when the MySQL container starts.

## 5. Communication Protocols

### 5.1. gRPC (Synchronous)

*   **Definition**: `protobufs/recommendations.proto` defines service contracts, messages, and RPCs.
*   **Usage**:
    *   **Preference Management**: Catalog UI triggers gRPC calls to Recommendations Service for:
        *   `CreatePreference(CreatePreferenceRequest) returns (GrpcPreference)`
        *   `GetPreferences(GetPreferencesRequest) returns (GetPreferencesResponse)`
        *   `DeletePreference(DeletePreferenceRequest) returns (DeletePreferenceResponse)`
    *   **Film Data Synchronization**: When a movie is added/edited in Catalog:
        *   Catalog calls `ProcessFilmUpdate(FilmUpdate) returns (FilmUpdateResponse)` on Recommendations Service.
        *   `FilmUpdate` message includes `film_id`, `title`, `description`, `genre`, `rating`, `release_year`, and extracted `keywords`.

### 5.2. Apache Kafka (Asynchronous, Event-Driven)

*   **Purpose**: To decouple the Recommendations Service (which identifies a recommendation) from the Catalog Service (which needs to know a film is recommended to update its display).
*   **Topic**: `film_recommendation_status_updates`
*   **Producer (Recommendations Service)**:
    *   After processing a `FilmUpdate` via gRPC and identifying a film as a match for a user's preference (and storing it in its local `recommendations` DB), it publishes a JSON message to this topic.
    *   Message format: `{'film_id': 'uuid-of-the-film', 'is_recommended': True}`.
    *   The `film_id` is used as the Kafka message key for potential partitioning.
*   **Consumer (Catalog Service)**:
    *   Runs in a background thread.
    *   Subscribes to `film_recommendation_status_updates`.
    *   On receiving a message, it parses the `film_id` and `is_recommended` status.
    *   It then updates the `is_recommended` field of the corresponding movie in its own `movies` database.

## 6. Docker & Docker Compose

*   **Dockerfiles**: Separate Dockerfiles for `catalog` and `recommendations` services define how their respective images are built (base Python image, copying code, installing dependencies from `requirements.txt`, setting entry points).
*   **`docker-compose.yaml`**:
    *   Orchestrates all services: `mysql`, `zookeeper`, `kafka`, `recommendations`, `catalog`.
    *   Defines service dependencies (e.g., services depending on `mysql` health, `kafka` depending on `zookeeper`).
    *   Manages networking, ensuring all services are on a common `microservices` network enabling DNS resolution by service name (e.g., `kafka:9092`, `mysql:3306`).
    *   Configures ports and environment variables (e.g., `DATABASE_URL`, `KAFKA_BROKER`).
    *   Mounts SQL initialization scripts into the MySQL container.
    *   Uses named volumes for MySQL data persistence (`mysql_data`).

## 7. Data Flow / Workflow Example

1.  **User Adds Preference**:
    *   User navigates to "My Preferences" in Catalog UI.
    *   User submits a new keyword (e.g., "action").
    *   Catalog Service makes a `CreatePreference` gRPC call to Recommendations Service.
    *   Recommendations Service saves `(user_id=1, keyword='action')` to its `prefs` table.

2.  **Admin Adds/Edits a Movie**:
    *   Admin uses Catalog UI to add a new movie "Action Movie Extravaganza" (genre: "action", etc.).
    *   Catalog Service saves this movie to its `movies` DB (with `is_recommended = False` initially).
    *   Catalog Service calls `notify_recommendations_service()`, which:
        *   Extracts keywords like {"action", "movie", "extravaganza"}.
        *   Sends a `FilmUpdate` gRPC message (with film details and these keywords) to Recommendations Service.

3.  **Recommendations Service Processes Film Update**:
    *   Receives the `FilmUpdate` gRPC message.
    *   Retrieves all user preferences (e.g., finds `(user_id=1, keyword='action')`).
    *   Compares film keywords with preference keywords. Finds a match ("action").
    *   Creates a new record in its `recommendations` table for `user_id=1` and the new film's ID, storing all film details (title, description, etc.).
    *   Publishes a message to Kafka topic `film_recommendation_status_updates`: `{'film_id': 'id-of-action-movie', 'is_recommended': True}`.

4.  **Catalog Service Consumes Kafka Message**:
    *   The background Kafka consumer thread in the Catalog Service polls the topic.
    *   Receives the message `{'film_id': 'id-of-action-movie', 'is_recommended': True}`.
    *   Looks up the movie with `id='id-of-action-movie'` in its `movies` database.
    *   Sets `movie.is_recommended = True` for this movie and commits the change.

5.  **User Views Recommendations**:
    *   User navigates to the "My Recommendations" page in the Catalog UI.
    *   The Catalog Service queries its `movies` table for `Movie.query.filter_by(is_recommended=True).all()`.
    *   "Action Movie Extravaganza" is now included in the list and displayed to the user.

## 8. Running the Project

1.  Ensure Docker and Docker Compose are installed.
2.  Clone the repository.
3.  From the project root, run `docker-compose down -v` (especially if schema changes occurred or to ensure a clean state, this removes volumes including database data).
4.  Run `docker-compose build` to build/rebuild service images if code has changed.
5.  Run `docker-compose up` to start all services.
    *   Catalog service will be available at `http://localhost:5000`.
    *   Recommendations gRPC service at `localhost:50051` (primarily for inter-service use, not directly accessed by user).
    *   Kafka broker at `localhost:9092` (if direct connection from host is needed for tools, services use `kafka:9092`).
## Simply go to `http://localhost:5000` to find the web app.

Thank you so much, made with passion -- Oumeyma jouini
