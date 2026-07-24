SELECT 'CREATE DATABASE vv_keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'vv_keycloak')\gexec
