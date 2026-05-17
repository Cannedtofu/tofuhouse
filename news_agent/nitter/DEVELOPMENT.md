# Local Nitter Instance Development Guide

This repository contains the configuration and scripts required to run a self-hosted instance of [Nitter](https://github.com/zedeus/nitter), a free and open-source alternative Twitter front-end focused on privacy and performance.

As of 2024, Twitter/X has removed unauthenticated access to its APIs. Consequently, Nitter now strictly requires valid user session tokens to scrape data. This document outlines how this project is structured to manage those tokens and easily spin up the Nitter service locally.

## Project Structure

- **`nitter.conf`**: The main configuration file for the Nitter instance. It has been pre-configured for local deployment, specifically adjusting the Redis host to connect to `nitter-redis` within the Docker network and disabling HTTPS for local testing (`localhost:8080`).
- **`sessions.jsonl`**: A newline-delimited JSON file that holds the session tokens for your Twitter accounts. Nitter uses these accounts in a round-robin fashion to query Twitter data. This file is mounted directly into the Docker container.
- **`docker-compose.yml`**: The Docker configuration file that defines the `nitter` web service and the `nitter-redis` caching database.
- **`tools/`**: A directory from the original Nitter repository containing Python scripts (like `create_session_curl.py`) used to authenticate Twitter accounts and generate the `sessions.jsonl` file.
- **`generate_tokens.bat`**: A custom Windows helper script that simplifies the execution of the Python token generation script. It prompts for a username and password and automatically appends the token to `sessions.jsonl`.
- **`start_nitter.bat`**: A custom Windows helper script to execute `docker-compose up -d`, launching the instance in the background.

## Prerequisites

To run and maintain this project, you will need:
1. **Docker Desktop**: Required to run the Nitter instance and Redis database container.
2. **Python 3.9+**: Required to run the session generation tools.
3. **Twitter/X Accounts**: You need at least one valid Twitter account.
   > **Warning:** It is highly recommended to use "burner" or dummy accounts. Because Nitter acts as a scraper, these accounts are at a high risk of being rate-limited, suspended, or permanently banned by Twitter.

## Setup Instructions

### 1. Python Environment Setup
Before generating tokens, ensure the required Python packages are installed:
```cmd
cd tools
pip install -r requirements.txt
cd ..
```

### 2. Generating Session Tokens
Nitter cannot start successfully without at least one valid session token. 
1. Run the `generate_tokens.bat` script located in the root directory.
2. Enter the Username and Password for your burner Twitter account when prompted.
3. The script will securely authenticate and append the generated token to `sessions.jsonl`. 
4. You can run this script multiple times to add a pool of accounts. Having multiple accounts reduces the rate-limiting burden on any single account.

### 3. Starting the Instance
Once your `sessions.jsonl` file is populated:
1. Ensure Docker Desktop is running.
2. Execute the `start_nitter.bat` script. This will pull the latest Nitter and Redis images and start them in the background.
3. Navigate to `http://localhost:8080` in your web browser.

## Maintenance and Troubleshooting

### Adding New Accounts
If Nitter begins to fail to load tweets (usually indicated by rate limit errors in the console), your current accounts may be temporarily restricted. Simply run `generate_tokens.bat` again with a new burner account to add it to the pool. Nitter automatically monitors `sessions.jsonl` and will pick up new tokens.

### Viewing Logs
To diagnose issues with Nitter, you can view the Docker logs:
```cmd
docker logs -f nitter
```

### Updating Nitter
To pull the latest version of Nitter and restart the container:
```cmd
docker-compose pull
docker-compose up -d
```

### Modifying Configuration
If you wish to host this instance publicly, you must edit `nitter.conf` to reflect your actual domain name (`hostname = "yourdomain.com"`), configure HTTPS, and set up a reverse proxy (like Nginx or Caddy). Refer to the official [Nitter Wiki](https://github.com/zedeus/nitter/wiki) for advanced deployment strategies.
