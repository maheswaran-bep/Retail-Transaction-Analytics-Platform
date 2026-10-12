
stage('Backend Tests') {
    steps {
        withCredentials([
            string(credentialsId: 'retail-db-password', variable: 'TEST_DB_PASSWORD'),
            string(credentialsId: 'retail-jwt-secret', variable: 'TEST_JWT_SECRET'),
            string(credentialsId: 'retail-admin-password', variable: 'TEST_ADMIN_PASSWORD')
        ]) {
            sh '''
                set -eu

                NETWORK="retail-test-${BUILD_NUMBER}"
                MYSQL_CONTAINER="retail-test-mysql-${BUILD_NUMBER}"

                cleanup() {
                    docker rm -f "$MYSQL_CONTAINER" >/dev/null 2>&1 || true
                    docker network rm "$NETWORK" >/dev/null 2>&1 || true
                }
                trap cleanup EXIT

                echo "===== Build Backend Test Image ====="
                docker build \
                    -f backend/Dockerfile \
                    -t retail-analytics-backend:ci .

                echo "===== Create Isolated Test Network ====="
                docker network create "$NETWORK"

                echo "===== Start Temporary MySQL ====="
                docker run -d \
                    --name "$MYSQL_CONTAINER" \
                    --network "$NETWORK" \
                    --network-alias mysql \
                    -e MYSQL_ROOT_PASSWORD="$TEST_DB_PASSWORD" \
                    -e MYSQL_DATABASE=retail_analytics_db \
                    mysql:8.4

                echo "===== Wait for MySQL ====="
                READY=0
                for i in $(seq 1 60); do
                    if docker exec "$MYSQL_CONTAINER" \
                        mysqladmin ping -h 127.0.0.1 \
                        -uroot -p"$TEST_DB_PASSWORD" --silent \
                        >/dev/null 2>&1; then
                        READY=1
                        break
                    fi
                    sleep 3
                done

                if [ "$READY" -ne 1 ]; then
                    docker logs "$MYSQL_CONTAINER"
                    echo "MySQL failed to become ready"
                    exit 1
                fi

                echo "===== Initialize Test Schema ====="
                docker run --rm \
                    --network "$NETWORK" \
                    -e DB_HOST=mysql \
                    -e DB_PORT=3306 \
                    -e DB_NAME=retail_analytics_db \
                    -e DB_USER=root \
                    -e DB_PASSWORD="$TEST_DB_PASSWORD" \
                    -e JWT_SECRET_KEY="$TEST_JWT_SECRET" \
                    -e ADMIN_DEFAULT_PASSWORD="$TEST_ADMIN_PASSWORD" \
                    retail-analytics-backend:ci \
                    python -m backend.database.init_db

                echo "===== Run Backend Tests ====="
                docker run --rm \
                    --network "$NETWORK" \
                    -e DB_HOST=mysql \
                    -e DB_PORT=3306 \
                    -e DB_NAME=retail_analytics_db \
                    -e DB_USER=root \
                    -e DB_PASSWORD="$TEST_DB_PASSWORD" \
                    -e JWT_SECRET_KEY="$TEST_JWT_SECRET" \
                    -e ADMIN_DEFAULT_PASSWORD="$TEST_ADMIN_PASSWORD" \
                    retail-analytics-backend:ci \
                    python -m pytest tests/ -v --tb=short

                echo "===== Backend Tests Completed ====="
            '''
        }
    }
}
