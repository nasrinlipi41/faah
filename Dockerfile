FROM python:3.11-slim

# পাইথন আউটপুট সরাসরি টার্মিনালে বা ক্লাউড লগে পাওয়ার জন্য
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# প্রয়োজনীয় ডিপেন্ডেন্সি ইনস্টল
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# সমস্ত প্রজেক্ট ফাইল কপি করা
COPY . .

# data ফোল্ডার নিশ্চিত করা
RUN mkdir -p data

EXPOSE 8080

# single worker + multi-thread মোড (in-memory state ও socket.io sync ঠিক রাখার জন্য)
CMD exec gunicorn --workers 1 --threads 8 --timeout 0 --bind 0.0.0.0:${PORT} app:app