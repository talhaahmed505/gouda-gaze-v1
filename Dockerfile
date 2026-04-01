# Use a slim Python image to keep the size down
FROM python:3.9-slim

# Install system-level dependencies (needed for OpenCV/Flask)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /gouda-gaze

# 1. Copy only the requirements file first (this helps with Docker caching)
COPY requirements.txt .

# 2. Install the Python packages listed in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy the rest of your project code
COPY . .

# Create logs directory
RUN mkdir -p logs

# Run the app
CMD ["python", "app.py"]