pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                sh '''
                    echo "Repository checked out successfully"
                    git log -1 --oneline
                '''
            }
        }

        stage('Prepare Environment') {
            steps {
                withCredentials([
                    string(
                        credentialsId: 'retail-db-password',
                        variable: 'DB_PASSWORD'
                    ),
                    string(
                        credentialsId: 'retail-jwt-secret',
                        variable: 'JWT_SECRET_KEY'
                    ),
                    string(
                        credentialsId: 'retail-admin-password',
                        variable: 'ADMIN_DEFAULT_PASSWORD'
                    )
                ]) {
                    sh '''
                        set -eu

                        cat > .env <<EOF
DB_HOST=mysql
DB_PORT=3306
DB_NAME=retail_analytics_db
DB_USER=root
DB_PASSWORD=${DB_PASSWORD}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
ADMIN_DEFAULT_PASSWORD=${ADMIN_DEFAULT_PASSWORD}
EOF

                        chmod 600 .env
                        echo "Environment file created"
                        docker compose config -q
                        echo "Docker Compose configuration is valid"
                    '''
                }
            }
        }

        

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
                DATA_VOLUME="retail-data-${BUILD_NUMBER}"
                DATA_LOADER="retail-data-loader-${BUILD_NUMBER}"
                DATASET_DIR="$WORKSPACE/dataset-download"
                ARCHIVE="$WORKSPACE/online_retail_ii.zip"

                cleanup() {
                    docker rm -f "$DATA_LOADER" >/dev/null 2>&1 || true
                    docker rm -f "$MYSQL_CONTAINER" >/dev/null 2>&1 || true
                    docker volume rm "$DATA_VOLUME" >/dev/null 2>&1 || true
                    docker network rm "$NETWORK" >/dev/null 2>&1 || true
                    rm -rf "$DATASET_DIR" "$ARCHIVE"
                }
                trap cleanup EXIT

                docker network create "$NETWORK"

                docker build \
                    -f backend/Dockerfile \
                    -t retail-analytics-backend:ci .

                docker run -d \
                    --name "$MYSQL_CONTAINER" \
                    --network "$NETWORK" \
                    --network-alias mysql \
                    -e MYSQL_ROOT_PASSWORD="$TEST_DB_PASSWORD" \
                    -e MYSQL_ROOT_HOST='%' \
                    -e MYSQL_DATABASE=retail_analytics_db \
                    mysql:8.4

                echo "Waiting for temporary MySQL database..."
                READY=0

                for i in $(seq 1 60); do
                    if docker exec "$MYSQL_CONTAINER" \
                        mysqladmin ping -h 127.0.0.1 -uroot \
                        "-p${TEST_DB_PASSWORD}" --silent >/dev/null 2>&1; then
                        READY=1
                        break
                    fi
                    sleep 3
                done

                if [ "$READY" -ne 1 ]; then
                    docker logs "$MYSQL_CONTAINER" || true
                    echo "MySQL failed to start"
                    exit 1
                fi

                docker run --rm --network "$NETWORK" \
                    -e DB_HOST=mysql \
                    -e DB_PORT=3306 \
                    -e DB_NAME=retail_analytics_db \
                    -e DB_USER=root \
                    -e DB_PASSWORD="$TEST_DB_PASSWORD" \
                    -e JWT_SECRET_KEY="$TEST_JWT_SECRET" \
                    -e ADMIN_DEFAULT_PASSWORD="$TEST_ADMIN_PASSWORD" \
                    --entrypoint python \
                    retail-analytics-backend:ci \
                    -m backend.database.init_db

                echo "Downloading and extracting retail dataset..."

                mkdir -p "$DATASET_DIR"

                docker run --rm \
                    -v "$WORKSPACE:/work" \
                    alpine:3.20 sh -ec '
                        apk add --no-cache curl unzip
                        curl -fL --retry 3 \
                          "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip" \
                          -o /work/online_retail_ii.zip
                        unzip -o /work/online_retail_ii.zip \
                          -d /work/dataset-download
                    '

                DATASET_FILE="$(find "$DATASET_DIR" -type f \
                    -iname 'online_retail_II.xlsx' -print -quit)"

                if [ -z "$DATASET_FILE" ]; then
                    echo "ERROR: Online Retail II workbook not found"
                    find "$DATASET_DIR" -type f
                    exit 1
                fi

                docker volume create "$DATA_VOLUME"

                docker create --name "$DATA_LOADER" \
                    -v "$DATA_VOLUME:/data" \
                    alpine:3.20 sleep 300

                docker cp "$DATASET_FILE" \
                    "$DATA_LOADER:/data/online_retail_II.xlsx"

                docker rm "$DATA_LOADER"

                echo "Importing retail transactions into temporary MySQL..."

                docker run --rm \
                    --network "$NETWORK" \
                    -v "$DATA_VOLUME:/data:ro" \
                    -e DB_HOST=mysql \
                    -e DB_PORT=3306 \
                    -e DB_NAME=retail_analytics_db \
                    -e DB_USER=root \
                    -e DB_PASSWORD="$TEST_DB_PASSWORD" \
                    -e JWT_SECRET_KEY="$TEST_JWT_SECRET" \
                    -e ADMIN_DEFAULT_PASSWORD="$TEST_ADMIN_PASSWORD" \
                    -e DATASET_PATH=/data/online_retail_II.xlsx \
                    --entrypoint python \
                    retail-analytics-backend:ci \
                    -m etl.pipeline --sheet all

                echo "Verifying imported sales data..."

                docker exec "$MYSQL_CONTAINER" \
                    mysql -uroot "-p${TEST_DB_PASSWORD}" \
                    retail_analytics_db \
                    -N -e "SELECT COUNT(*) FROM fact_sales;" \
                    > "$WORKSPACE/fact_sales_count.txt"

                FACT_COUNT="$(cat "$WORKSPACE/fact_sales_count.txt")"
                echo "Imported fact_sales rows: $FACT_COUNT"

                if [ "$FACT_COUNT" -le 0 ]; then
                    echo "ERROR: ETL completed without loading sales records"
                    exit 1
                fi

                echo "Running backend test suite..."

                docker run --rm \
                    --network "$NETWORK" \
                    -v "$DATA_VOLUME:/data:ro" \
                    -e DB_HOST=mysql \
                    -e DB_PORT=3306 \
                    -e DB_NAME=retail_analytics_db \
                    -e DB_USER=root \
                    -e DB_PASSWORD="$TEST_DB_PASSWORD" \
                    -e JWT_SECRET_KEY="$TEST_JWT_SECRET" \
                    -e ADMIN_DEFAULT_PASSWORD="$TEST_ADMIN_PASSWORD" \
                    -e DATASET_PATH=/data/online_retail_II.xlsx \
                    --entrypoint python \
                    retail-analytics-backend:ci \
                    -m pytest tests/ -v --tb=short
            '''
        }
    }
}



        stage('Build Images') {
            steps {
                sh '''
                    set -eu
                    docker compose build
                    echo "Docker images built successfully"
                '''
            }
        }

        stage('Deploy') {
            steps {
                sh '''
                    set -eu
                    docker compose up -d
                    docker compose ps
                '''
            }
        }

        stage('Verify Deployment') {
            steps {
                sh '''
                    set -eu

                    echo "Checking backend health endpoint..."

                    READY=0

                    for i in $(seq 1 30); do
                        if docker compose exec -T backend \
                            python -c \
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" \
                            >/dev/null 2>&1; then
                            READY=1
                            break
                        fi

                        sleep 3
                    done

                    if [ "$READY" -ne 1 ]; then
                        echo "Backend health check failed"
                        docker compose ps
                        docker compose logs --tail=100 backend
                        exit 1
                    fi

                    echo "Backend health check passed"
                    docker compose ps
                '''
            }
        }
    }

    post {
        always {
            echo 'Pipeline finished. Displaying container status.'
            sh 'docker compose ps || true'
        }

        success {
            echo 'Retail Transaction Analytics Platform pipeline succeeded.'
        }

        failure {
            echo 'Pipeline failed. Review the Console Output for details.'
        }
    }
}