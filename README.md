# SMVS Approval - Project Structure

## Quick Start
```bash
# Start the application
python manage.py runserver 0.0.0.0:9000

# Visit: http://localhost:9000
```

## Project Files

### Core Application Files
- **manage.py** - Django management script
- **requirements.txt** - Python dependencies
- **.env** - PostgreSQL configuration (DO NOT COMMIT)

### Configuration
- **smvs_approval_config/** - Django settings and URLs
  - settings.py - Database configuration (uses PostgreSQL)
  - urls.py - URL routing
  - wsgi.py - WSGI application

### Application Modules
- **approval_core/** - Core approval system
- **approval_workflow/** - Workflow management

### Static Files & Media
- **static/** - CSS, JavaScript, images
- **templates/** - HTML templates
- **media/** - User uploaded files

### Docker Support
- **Dockerfile** - Containerization for production
- **docker-compose.yml** - Local development with PostgreSQL

### Utilities
- **setup_initial_data.py** - Initialize system data
- **verify_database.py** - Verify PostgreSQL connection

### Database
- **.env** - PostgreSQL credentials
  ```
  DB_ENGINE=django.db.backends.postgresql
  DB_HOST=localhost
  DB_PORT=5433
  DB_NAME=smvs_approval
  DB_USER=postgres
  DB_PASSWORD=Admin@123
  ```

## Database
- **Type**: PostgreSQL 18.3
- **Host**: localhost:5433
- **Database**: smvs_approval
- **Tables**: 30 (all Django models)

## Development Commands

```bash
# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver 0.0.0.0:9000

# Collect static files (production)
python manage.py collectstatic --noinput

# Verify database connection
python verify_database.py
```

## Production Deployment

```bash
# Using Docker
docker-compose up -d

# Or manually with Gunicorn
gunicorn smvs_approval_config.wsgi:application --bind 0.0.0.0:9000
```

## Important Notes

- All data is stored in PostgreSQL (not SQLite)
- .env file contains sensitive credentials
- Do NOT commit .env to version control
- Old SQLite database (db.sqlite3) can be safely deleted

'''🚀 Production Deployment Commands:
Whenever you update this block or push changes live to your production Docker container, remember to update the server's crontab table space by executing this command inside your terminal:
docker exec -it smvs_django_app python manage.py crontab add
'''
