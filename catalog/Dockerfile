FROM python:3.9-slim

WORKDIR /app

# Install MySQL client and verify installation
RUN apt-get update && \
    apt-get install -y default-mysql-client \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config && \
    mysql --version && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY catalog/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project structure
COPY . .

# Generate gRPC code
RUN python -m grpc_tools.protoc -I protobufs --python_out=catalog \
    --grpc_python_out=catalog protobufs/recommendations.proto

WORKDIR /app/catalog

# Wait for MySQL to be ready
COPY catalog/wait-for-it.sh /wait-for-it.sh
RUN chmod +x /wait-for-it.sh

# Debug: Print the contents of wait-for-it.sh
RUN cat /wait-for-it.sh

CMD ["/wait-for-it.sh", "mysql", "--", "python", "catalog.py"]