# LaTeX to PDF Conversion Service

A high-performance, secure REST API for converting LaTeX documents to PDF format.

## Features

- **Simple API**: Upload a ZIP file containing LaTeX documents and get a PDF back
- **Secure Processing**: Comprehensive security measures including input validation and sanitization
- **Multiple Workers**: Designed for concurrency with shared file system and SQLite database
- **Flexible ZIP Layout**: Supports `main.tex` either at the root or inside a single top-level folder (e.g., GitHub or Overleaf exports)
- **Robust Error Handling**: Detailed error messages with LaTeX compilation logs
- **Automatic Cleanup**: Background process removes expired PDFs and temporary files
- **Configurable Options**: custom main file name
- **API Key Authentication**: Optional security layer with configurable API keys
- **Rate Limiting**: Protection against API abuse
- **Resource Control**: Limits on file sizes and compilation time
- **Docker Ready**: Ready-to-use Docker and Docker Compose configurations

## Quick Start

The easiest way to run the service is with Docker Compose:

```bash
# Clone the repository
git clone https://github.com/rbehzadan/tex2pdf.git
cd tex2pdf

# Start the service
docker-compose up -d
```

The service will be available at `http://localhost:8000`.

## API Usage

### Convert LaTeX to PDF

```bash
curl -X POST \
  -H "X-API-Key: 1234" \
  -F "zip_file=@my_latex_files.zip" \
  http://localhost:8000/tex2pdf
```

Response:
```json
{
  "job_id": "28f5bf9b-587f-4f3c-a3de-4d737d9736ce",
  "status": "processing",
  "message": "Conversion job started"
}
```

### Check Job Status

```bash
curl -X GET \
  -H "X-API-Key: 1234" \
  http://localhost:8000/tex2pdf/status/28f5bf9b-587f-4f3c-a3de-4d737d9736ce
```

Response:
```json
{
  "job_id": "28f5bf9b-587f-4f3c-a3de-4d737d9736ce",
  "status": "completed",
  "created_at": 1741424390.6039968
}
```

### Download PDF

```bash
curl -X GET \
  -H "X-API-Key: 1234" \
  -o output.pdf \
  http://localhost:8000/tex2pdf/download/28f5bf9b-587f-4f3c-a3de-4d737d9736ce
```

### Health Check

```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "database": "connected",
  "storage": true
}
```

## Advanced Usage

### Compilation Options

You can specify the main LaTeX file if it's not named `main.tex`:

```bash
curl -X POST \
  -H "X-API-Key: 1234" \
  -F "zip_file=@my_latex_files.zip" \
  -F "options={\"main_file\": \"document.tex\"}" \
  http://localhost:8000/tex2pdf
```

Option:

* `main_file`: Name of the main LaTeX file to compile (default: `main.tex`)

ℹ️ The service now uses `latexmk` for automatic multiple runs and bibliography support, so you no longer need to set `num_runs` or `use_bibtex`.

## ZIP File Requirements

- The ZIP file must contain all necessary files for LaTeX compilation
- The service looks for `main.tex` (or your specified `main_file`) either:
  - At the root of the ZIP file, **or**
  - Inside a single top-level folder (e.g., `myproject/main.tex`)
- All referenced files (images, styles, etc.) should be included
- Paths in LaTeX files should be relative and match the ZIP structure

## Configuration

The service can be configured via environment variables in the docker-compose.yml file:

| Variable | Description | Default |
|----------|-------------|---------|
| `ALLOWED_API_KEYS` | Comma-separated list of valid API keys | "" (empty = no auth) |
| `API_KEY_REQUIRED` | Enable/disable API key validation | "true" |
| `MAX_WORKERS` | Number of uvicorn workers | 2 |
| `MAX_UPLOAD_SIZE` | Maximum file upload size in bytes | 52428800 (50MB) |
| `MAX_COMPILATION_TIME` | Maximum LaTeX compilation time in seconds | 240 |
| `RATE_LIMIT_WINDOW` | Rate limiting window in seconds | 60 |
| `MAX_REQUESTS_PER_WINDOW` | Maximum requests per rate limit window | 10 |
| `JOB_EXPIRY` | Job expiry time in seconds | 3600 (1 hour) |
| `JOBS_DIR` | Directory for storing PDF files | "/data/jobs" |
| `DB_PATH` | Path to SQLite database | "/data/db/jobs.db" |

## Deployment

### System Requirements

- Docker and Docker Compose
- For running without Docker:
  - Python 3.10+
  - LaTeX distribution (texlive)
  - SQLite3

### Production Deployment Considerations

For production deployments, consider:

1. **Configure a reverse proxy** (like Nginx) with HTTPS
2. **Adjust resource limits** based on your workload
3. **Set strong API keys** and restrict access
4. **Mount persistent volumes** for job data
5. **Monitor disk usage** and adjust `JOB_EXPIRY` accordingly
6. **Set up logging** to a centralized logging service

## Architecture

The service uses a stateless design with background processing:

1. **FastAPI Application**: Handles HTTP requests and responses
2. **SQLite Database**: Stores job metadata and status
3. **File System**: Stores generated PDFs and temporary files
4. **Background Tasks**: Process LaTeX compilation asynchronously

## Development

### Local Development Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/tex2pdf.git
cd tex2pdf

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the service
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Running Tests

```bash
pytest tests/
```

## License

[MIT License](LICENSE)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Security Considerations

While this service implements several security measures:

- API key authentication
- Input validation
- Rate limiting
- Safe ZIP extraction
- Process isolation

Be aware that allowing users to run LaTeX compilation on your server carries inherent risks. Always deploy behind a secure gateway in production environments.

