# Automated RSS News Agent

This project is a Python-based tool that automatically fetches articles from RSS feeds, generates summaries using the Gemini API, and emails them as a daily digest. The entire process is automated using GitHub Actions.

## Features

- Fetches new articles from a configurable list of RSS feeds.
- Uses a local `history.json` to track processed articles and avoid duplicates.
- Generates concise summaries for each new article using the Google Gemini API.
- Aggregates summaries into a single Markdown-formatted email.
- Sends the email via SMTP.
- Fully automated with a scheduled GitHub Actions workflow.

## Project Structure

```
news_agent/
├── .github/
│   └── workflows/
│       └── daily_report.yml  # GitHub Actions workflow
├── main.py                   # Main application script
├── requirements.txt          # Python dependencies
├── history.json              # Stores URLs of processed articles
└── README.md                 # This file
```

## Local Development Setup

To run the agent on your local machine, follow these steps:

1.  **Clone the Repository:**
    ```bash
    git clone <your-repo-url>
    cd news_agent
    ```

2.  **Create a Virtual Environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables:**
    Create a file named `.env` in the `news_agent` directory and add the following variables. This file is ignored by Git to keep your secrets safe.

    ```env
    # Gemini API Key
    GEMINI_API_KEY="YOUR_GEMINI_API_KEY"

    # Email Configuration
    SMTP_USER="your_email@example.com"
    SMTP_PASSWORD="your_email_app_password"
    SMTP_SERVER="smtp.example.com"
    SMTP_PORT="587"

    # Recipient Email
    RECIPIENT_EMAIL="recipient_email@example.com"
    ```

5.  **Run the Script:**
    ```bash
    python main.py
    ```

## GitHub Actions Automation Setup

To enable the automated workflow in your GitHub repository, you need to configure repository secrets.

1.  Navigate to your GitHub repository.
2.  Go to `Settings` > `Secrets and variables` > `Actions`.
3.  Click `New repository secret` for each of the following secrets and add the corresponding values:

    - `GEMINI_API_KEY`: Your Google Gemini API key.
    - `SMTP_USER`: The username for your SMTP email account (e.g., your email address).
    - `SMTP_PASSWORD`: The application-specific password for your email account.
    - `SMTP_SERVER`: The address of your SMTP server.
    - `SMTP_PORT`: The port for your SMTP server (e.g., 587 for TLS).
    - `RECIPIENT_EMAIL`: The email address where the digest will be sent.

The workflow is scheduled to run daily at midnight UTC. It will automatically run the script, and if `history.json` is updated, it will commit and push the change back to the repository.
