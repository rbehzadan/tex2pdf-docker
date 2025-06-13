from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, FileResponse
from io import BytesIO
import asyncio
import tempfile
import zipfile
import os
import logging
import shutil
import re
import uuid
import json
import time
from typing import Optional, Dict, List, Any
from pathlib import Path
import contextlib
from pydantic import BaseModel, Field
import sqlite3
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("tex2pdf-service")

app = FastAPI(title="LaTeX to PDF Conversion Service")

# Configuration
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", 50 * 1024 * 1024))  # Default: 50 MB
API_KEY_NAME = os.environ.get("API_KEY_NAME", "X-API-Key")
ALLOWED_API_KEYS = os.environ.get("ALLOWED_API_KEYS", "").split(",")
MAX_COMPILATION_TIME = int(os.environ.get("MAX_COMPILATION_TIME", 240))  # Default: 240 seconds
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", 60))  # Default: 60 seconds
MAX_REQUESTS_PER_WINDOW = int(os.environ.get("MAX_REQUESTS_PER_WINDOW", 10))  # Default: 10 requests
JOB_EXPIRY = int(os.environ.get("JOB_EXPIRY", 3600))  # Default: 1 hour
JOBS_DIR = os.environ.get("JOBS_DIR", "/app/jobs")
DB_PATH = os.environ.get("DB_PATH", "/app/db/jobs.db")
API_KEY_REQUIRED = len(ALLOWED_API_KEYS) > 0
if API_KEY_REQUIRED:
    API_KEY_REQUIRED = os.environ.get("API_KEY_REQUIRED", "true").lower() in ("true", "1", "yes")
VERSION=open("VERSION").read().strip()

# Create necessary directories
os.makedirs(JOBS_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Initialize SQLite database
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            work_dir TEXT,
            api_key TEXT,
            options TEXT,
            error TEXT,
            progress TEXT,
            updated_at REAL NOT NULL
        )
        ''')
        # Add index for faster lookups
        conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)')

# Thread pool for database operations
executor = ThreadPoolExecutor(max_workers=4)

# In-memory rate limiting
rate_limits: Dict[str, List[float]] = {}

class ConversionOptions(BaseModel):
    main_file: str = Field(default="main.tex", description="Main LaTeX file to compile")
    num_runs: int = Field(default=2, ge=1, le=5, description="Number of compilation runs")
    use_bibtex: bool = Field(default=False, description="Run BibTeX for bibliography")

def verify_api_key(request: Request):
    # If API keys are not required, skip validation
    if not API_KEY_REQUIRED:
        return "no_auth"

    api_key = request.headers.get(API_KEY_NAME)

    # Check if API key is provided and valid
    if not api_key:
        logger.warning("Missing API key in request")
        raise HTTPException(
            status_code=401,
            detail="API key required",
        )

    if not ALLOWED_API_KEYS or api_key not in ALLOWED_API_KEYS:
        logger.warning(f"Unauthorized access attempt with API key: {api_key[:5]}...")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
        )

    return api_key

def check_rate_limit(request: Request, api_key: str = Depends(verify_api_key)):
    client_id = api_key or request.client.host
    current_time = time.time()

    if client_id not in rate_limits:
        rate_limits[client_id] = []

    # Remove timestamps outside the window
    rate_limits[client_id] = [t for t in rate_limits[client_id] if current_time - t < RATE_LIMIT_WINDOW]

    if len(rate_limits[client_id]) >= MAX_REQUESTS_PER_WINDOW:
        logger.warning(f"Rate limit exceeded for {client_id[:5]}...")
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {MAX_REQUESTS_PER_WINDOW} requests per {RATE_LIMIT_WINDOW} seconds.",
        )

    rate_limits[client_id].append(current_time)
    return client_id

def validate_latex_filename(filename: str) -> bool:
    """Validate if the filename follows safe LaTeX filename conventions."""
    return bool(re.match(r'^[a-zA-Z0-9_\-\.]+\.tex$', filename))

def sanitize_zip_archive(zip_file_obj, extract_path):
    """Extracts zip contents safely, preventing directory traversal attacks."""
    try:
        with zipfile.ZipFile(zip_file_obj) as zip_ref:
            # Log zip contents for debugging
            logger.info(f"ZIP contents: {zip_ref.namelist()}")

            # First, check for suspicious paths
            for file_info in zip_ref.infolist():
                # Convert to Path for safer path handling
                file_path = Path(file_info.filename)

                # Check for absolute paths or directory traversal attempts
                if file_path.is_absolute() or '..' in file_path.parts:
                    raise ValueError(f"Suspicious path detected: {file_info.filename}")

                # Check for extremely large files
                if file_info.file_size > MAX_UPLOAD_SIZE:
                    raise ValueError(f"File too large: {file_info.filename}")

            # If all files pass validation, extract them
            for file_info in zip_ref.infolist():
                # Skip directories
                if file_info.filename.endswith('/'):
                    continue

                # Create a safe extraction path
                target_path = Path(extract_path) / file_info.filename

                # Create parent directories if they don't exist
                target_path.parent.mkdir(parents=True, exist_ok=True)

                # Extract the file
                with zip_ref.open(file_info) as source, open(target_path, 'wb') as target:
                    shutil.copyfileobj(source, target)

            # List extracted files for debugging
            extracted_files = list(Path(extract_path).glob('**/*'))
            logger.info(f"Extracted files: {[str(f.relative_to(extract_path)) for f in extracted_files]}")

        return True
    except zipfile.BadZipFile:
        raise ValueError("Invalid ZIP file format")
    except Exception as e:
        logger.error(f"Error during ZIP extraction: {str(e)}", exc_info=True)
        raise ValueError(f"Error extracting ZIP: {str(e)}")

async def run_latex_command(cmd, cwd=None, timeout=MAX_COMPILATION_TIME):
    """Run a LaTeX-related command in a specified working directory."""
    logger.info(f"Running command: {' '.join(cmd)} in {cwd}")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout
        )

        stdout_text = stdout.decode('utf-8', errors='replace')
        stderr_text = stderr.decode('utf-8', errors='replace')

        logger.info(f"Command returned with code {process.returncode}")
        if process.returncode != 0:
            logger.warning(f"Command failed with stderr: {stderr_text[:500]}...")

        return {
            "returncode": process.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text
        }
    except asyncio.TimeoutError:
        logger.error(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
        raise TimeoutError(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")

# Database operations
def store_job(job_id: str, job_data: Dict[str, Any]):
    """Store job data in SQLite database"""
    current_time = time.time()

    # Extract fields from job_data
    status = job_data.get("status", "unknown")
    created_at = job_data.get("created_at", current_time)
    work_dir = job_data.get("work_dir", "")
    api_key = job_data.get("api_key", "")
    options = json.dumps(job_data.get("options", {}))
    error = job_data.get("error", "")
    progress = job_data.get("progress", "")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            '''
            INSERT OR REPLACE INTO jobs
            (id, status, created_at, work_dir, api_key, options, error, progress, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (job_id, status, created_at, work_dir, api_key, options, error, progress, current_time)
        )
        conn.commit()

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve job data from SQLite database"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,))
        row = cursor.fetchone()

    if row:
        job_data = dict(row)
        # Parse options back to dict
        if job_data.get('options'):
            job_data['options'] = json.loads(job_data['options'])
        return job_data
    return None

def update_job(job_id: str, updates: Dict[str, Any]):
    """Update specific fields in the job data"""
    current_time = time.time()

    # Start with SET updated_at=?
    set_values = ["updated_at=?"]
    params = [current_time]

    # Add each update field
    for key, value in updates.items():
        if key == 'options':
            value = json.dumps(value)
        set_values.append(f"{key}=?")
        params.append(value)

    # Add job_id as the last parameter
    params.append(job_id)

    with sqlite3.connect(DB_PATH) as conn:
        query = f"UPDATE jobs SET {', '.join(set_values)} WHERE id = ?"
        conn.execute(query, params)
        conn.commit()

def get_pdf_path(job_id: str) -> str:
    """Get the path where the PDF should be stored"""
    return os.path.join(JOBS_DIR, f"{job_id}.pdf")

def store_pdf(job_id: str, pdf_content: bytes):
    """Store PDF in the filesystem"""
    pdf_path = get_pdf_path(job_id)
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    with open(pdf_path, 'wb') as f:
        f.write(pdf_content)

def get_pdf(job_id: str) -> Optional[bytes]:
    """Retrieve PDF from the filesystem"""
    pdf_path = get_pdf_path(job_id)
    if os.path.exists(pdf_path):
        with open(pdf_path, 'rb') as f:
            return f.read()
    return None

async def compile_latex(
    job_id: str,
    work_dir: str,
    main_file: str,
    num_runs: int,
    use_bibtex: bool
):
    """Compile LaTeX document with proper error handling and multiple runs if needed."""
    results = []
    main_tex_path = os.path.join(work_dir, main_file)

    # Verify the main file exists
    if not os.path.exists(main_tex_path):
        logger.error(f"Main LaTeX file not found: {main_tex_path}")
        update_job(job_id, {
            "status": "failed",
            "error": f"Main LaTeX file ({main_file}) not found in the archive."
        })
        return False

    # List directory contents for debugging
    logger.info(f"Work directory contents: {os.listdir(work_dir)}")

    try:
        # Run pdflatex multiple times as needed
        for i in range(num_runs):
            update_job(job_id, {
                "status": "processing",
                "progress": f"LaTeX compilation {i+1}/{num_runs}"
            })

            # For verbose output to diagnose issues
            cmd = [
                'pdflatex',
                '-interaction=nonstopmode',
                '-file-line-error',
                main_file
            ]

            try:
                result = await run_latex_command(cmd, cwd=work_dir)
                results.append(result)

                # If compilation failed, stop and provide details
                if result["returncode"] != 0:
                    # Extract relevant error messages
                    error_lines = []
                    for line in result["stdout"].split('\n'):
                        if ":" in line and ("Error" in line or "Fatal" in line):
                            error_lines.append(line)

                    error_message = "LaTeX compilation failed"
                    if error_lines:
                        error_message = f"LaTeX errors: {' | '.join(error_lines[:3])}"

                    update_job(job_id, {
                        "status": "failed",
                        "error": error_message,
                        "details": json.dumps(result)
                    })
                    return False

                # Run bibtex if requested (after the first pdflatex run)
                if use_bibtex and i == 0:
                    update_job(job_id, {
                        "status": "processing",
                        "progress": "Running BibTeX"
                    })

                    basename = os.path.splitext(main_file)[0]
                    bibtex_cmd = ['bibtex', basename]

                    bibtex_result = await run_latex_command(bibtex_cmd)
                    results.append(bibtex_result)

            except TimeoutError as e:
                logger.error(f"Timeout during compilation: {str(e)}")
                update_job(job_id, {
                    "status": "failed",
                    "error": str(e)
                })
                return False
            except Exception as e:
                logger.error(f"Unexpected error during compilation: {str(e)}", exc_info=True)
                update_job(job_id, {
                    "status": "failed",
                    "error": f"Unexpected error: {str(e)}"
                })
                return False

        # Check if the PDF was generated
        pdf_basename = os.path.splitext(main_file)[0]
        pdf_path = os.path.join(work_dir, f"{pdf_basename}.pdf")

        if not os.path.exists(pdf_path):
            logger.error(f"PDF not generated at expected path: {pdf_path}")
            update_job(job_id, {
                "status": "failed",
                "error": "PDF file not generated despite successful compilation"
            })
            return False

        # Store the PDF in the filesystem
        with open(pdf_path, 'rb') as f:
            pdf_content = f.read()
            store_pdf(job_id, pdf_content)

        # Update job status
        update_job(job_id, {
            "status": "completed",
        })
        return True

    except Exception as e:
        logger.error(f"Exception in compile_latex: {str(e)}", exc_info=True)
        update_job(job_id, {
            "status": "failed",
            "error": f"Unexpected error: {str(e)}"
        })
        return False

# Clean up old jobs (runs in background)
async def cleanup_old_jobs():
    """Clean up old jobs and their resources"""
    while True:
        try:
            current_time = time.time()
            expiry_time = current_time - JOB_EXPIRY

            # Get expired jobs
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('SELECT id, work_dir FROM jobs WHERE created_at < ?', (expiry_time,))
                expired_jobs = cursor.fetchall()

            for job in expired_jobs:
                job_id = job['id']
                work_dir = job['work_dir']

                # Clean up PDF if it exists
                pdf_path = get_pdf_path(job_id)
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)

                # Clean up work directory if it exists
                if work_dir and os.path.exists(work_dir):
                    shutil.rmtree(work_dir, ignore_errors=True)

                # Remove job from database
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
                    conn.commit()

                logger.info(f"Cleaned up expired job {job_id}")

        except Exception as e:
            logger.error(f"Error in cleanup task: {str(e)}", exc_info=True)

        # Run cleanup every 15 minutes
        await asyncio.sleep(900)

@app.post("/tex2pdf",
          dependencies=[Depends(check_rate_limit)],
          summary="Convert LaTeX files to PDF",
          response_description="Returns job ID for status checking")
async def convert_to_pdf(
    background_tasks: BackgroundTasks,
    request: Request,
    zip_file: UploadFile = File(...),
    options: Optional[ConversionOptions] = None
):
    """
    Takes a zip file containing LaTeX files and compiles them into a PDF.

    - The zip file must contain all necessary files for compilation
    - By default, assumes main.tex is the main file unless specified otherwise
    - Returns a job ID that can be used to check status and retrieve the PDF
    """
    api_key = verify_api_key(request)
    start_time = time.time()
    job_id = str(uuid.uuid4())

    if options is None:
        options = ConversionOptions()

    logger.info(f"Starting conversion job {job_id}")

    # Validate input
    if not zip_file.filename.endswith('.zip'):
        logger.warning(f"Job {job_id}: Invalid file format: {zip_file.filename}")
        raise HTTPException(
            status_code=400,
            detail="Uploaded file must be a zip archive."
        )

    if not validate_latex_filename(options.main_file):
        logger.warning(f"Job {job_id}: Invalid main file name: {options.main_file}")
        raise HTTPException(
            status_code=400,
            detail="Main file name must be a valid LaTeX filename (e.g., main.tex)"
        )

    # Create the job record
    job_data = {
        "id": job_id,
        "status": "uploading",
        "created_at": start_time,
        "options": options.dict(),
        "api_key": api_key,
    }
    store_job(job_id, job_data)

    try:
        # Create a temporary directory for this job
        work_dir = tempfile.mkdtemp(prefix=f"tex2pdf_{job_id}_")
        update_job(job_id, {
            "status": "extracting",
            "work_dir": work_dir
        })

        # Read zip file to memory
        zip_content = await zip_file.read()
        if len(zip_content) > MAX_UPLOAD_SIZE:
            logger.warning(f"Job {job_id}: File too large: {len(zip_content)} bytes")
            update_job(job_id, {
                "status": "failed",
                "error": f"File too large. Maximum size: {MAX_UPLOAD_SIZE/1024/1024} MB"
            })
            return {
                "job_id": job_id,
                "status": "failed",
                "message": "File too large"
            }

        # Extract zip files safely
        try:
            sanitize_zip_archive(BytesIO(zip_content), work_dir)
            update_job(job_id, {"status": "queued"})
        except ValueError as e:
            logger.warning(f"Job {job_id}: Zip extraction failed: {str(e)}")
            update_job(job_id, {
                "status": "failed",
                "error": f"Zip extraction failed: {str(e)}"
            })
            return {
                "job_id": job_id,
                "status": "failed",
                "message": str(e)
            }

        # Start compilation in background
        background_tasks.add_task(
            compile_latex,
            job_id,
            work_dir,
            options.main_file,
            options.num_runs,
            options.use_bibtex
        )

        return {
            "job_id": job_id,
            "status": "processing",
            "message": "Conversion job started"
        }

    except Exception as e:
        logger.error(f"Job {job_id}: Unexpected error: {str(e)}", exc_info=True)
        update_job(job_id, {
            "status": "failed",
            "error": f"Unexpected error: {str(e)}"
        })
        return {
            "job_id": job_id,
            "status": "failed",
            "message": "Server error"
        }

@app.get("/tex2pdf/status/{job_id}",
         dependencies=[Depends(verify_api_key)],
         summary="Check the status of a conversion job")
async def check_job_status(job_id: str):
    """Check the status of a previously submitted conversion job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    # Clean sensitive or internal information
    response = {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"],
    }

    # Add error details if failed
    if job["status"] == "failed" and "error" in job:
        response["error"] = job["error"]

    # Add progress info if processing
    if job["status"] == "processing" and "progress" in job:
        response["progress"] = job["progress"]

    return response

@app.get("/tex2pdf/download/{job_id}",
         dependencies=[Depends(verify_api_key)],
         summary="Download the generated PDF")
async def download_pdf(job_id: str):
    """Download the PDF generated by a completed conversion job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    if job["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"PDF not ready. Current status: {job['status']}"
        )

    try:
        # Option 1: Get PDF from memory and stream it
        # pdf_content = get_pdf(job_id)
        # if not pdf_content:
        #     raise HTTPException(
        #         status_code=404,
        #         detail="PDF file not found in storage"
        #     )
        #
        # # Generate a filename based on the job ID
        # filename = f"document_{job_id[-6:]}.pdf"
        #
        # return StreamingResponse(
        #     BytesIO(pdf_content),
        #     media_type='application/pdf',
        #     headers={"Content-Disposition": f"attachment; filename={filename}"}
        # )

        # Option 2: Use FileResponse for more efficient file serving
        pdf_path = get_pdf_path(job_id)
        if not os.path.exists(pdf_path):
            raise HTTPException(
                status_code=404,
                detail="PDF file not found in storage"
            )

        filename = f"document_{job_id[-6:]}.pdf"

        return FileResponse(
            pdf_path,
            media_type='application/pdf',
            filename=filename
        )
    except Exception as e:
        logger.error(f"Error delivering PDF for job {job_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Error retrieving PDF file"
        )

@app.get("/health", summary="Health check endpoint")
async def health_check():
    """Simple health check endpoint to verify the API is running."""
    try:
        # Check database connection
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("SELECT 1")
            cursor.fetchone()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "version": VERSION,
        "database": db_status,
        "storage": os.path.exists(JOBS_DIR) and os.access(JOBS_DIR, os.W_OK)
    }

@app.on_event("startup")
async def startup_event():
    logger.info("Service starting up")
    # Initialize the database
    init_db()
    # Start background cleanup task
    asyncio.create_task(cleanup_old_jobs())

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown"""
    logger.info("Service shutting down")
    executor.shutdown(wait=False)
