#!/bin/bash
# wait-for-it.sh

set -e

host="$1"
shift
cmd="$@"

echo "Waiting for MySQL to be ready..."
echo "Testing MySQL connection..."

# Try to connect to MySQL and show any errors
until mysql -h "$host" -P 3306 -u soaproject -psoaproject -e "SELECT 1" 2>&1; do
  echo "MySQL is unavailable - sleeping"
  echo "Last error: $?"
  sleep 5
done

echo "MySQL is up - executing command"
exec $cmd 