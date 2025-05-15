FROM python:3.9-slim

WORKDIR /app

# Install system dependencies required for mysqlclient
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY recommendations/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project structure
COPY . .

# Create .env file with database configuration
RUN echo "DATABASE_URL=mysql://soaproject:soaproject@mysql:3306/recommendations" > .env

# Generate gRPC code
RUN python -m grpc_tools.protoc -I protobufs --python_out=recommendations \
    --grpc_python_out=recommendations protobufs/recommendations.proto

WORKDIR /app/recommendations

EXPOSE 50051
CMD ["python", "recommendations.py"]