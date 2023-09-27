import requests
from bs4 import BeautifulSoup
import time
import json
import os
import logging
import sys
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,  # Logging to stdout
)

# Get login and password from environment variables
blue_login = os.getenv("BLUE_LOGIN", "user")
blue_password = os.getenv("BLUE_PASSWORD", "password")

# Base URL of the ResetEra gaming forum with query parameters for ordering
base_url = (
    "https://www.resetera.com/forums/gaming-forum.7/?order=post_date&direction=desc"
)

# Get the current Unix time and subtract 7200 seconds (2 hours) as the default value
default_unix_time = int(time.time()) - 7200

# Check for an environment variable named PAGES_TO_LOAD, use 1 as the default value if not provided
pages_to_load = int(os.getenv("PAGES_TO_LOAD", 1))

# Name of the timestamp file
timestamp_file = "timestamp"

# Try to read the Unix time from the timestamp file, use the default value if the file doesn't exist
try:
    with open(timestamp_file, "r") as file:
        content = file.read().strip()
        if not content:  # Check if the file is empty
            raise ValueError("Timestamp file is empty")
        input_unix_time = int(content)
except (FileNotFoundError, ValueError) as e:
    logging.warning(f"Using default_unix_time due to error: {e}")
    input_unix_time = default_unix_time

# Initialize the list to hold the threads
threads_list = []

# Loop through the specified number of pages
for page in range(1, pages_to_load + 1):
    url = f"{base_url}&page={page}"

    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to retrieve page {page}. Error: {e}")
        continue

    if response.status_code == 200:
        # Parse the HTML content of the page
        soup = BeautifulSoup(response.text, "html.parser")

        # Find the topics on the page
        topics = soup.select(".structItem-title a")

        # Loop over the topics and add them to the list along with the time and link
        for topic in topics:
            # Find the parent 'structItem' div of the topic
            struct_item = topic.find_parent("div", class_="structItem")

            if struct_item:
                # Check if the item has an element with class 'structItem-status--sticky', if so, skip it
                if struct_item.select_one(".structItem-status--sticky"):
                    continue

                # Find the time tag within the 'structItem' div
                time_tag = struct_item.find("time", class_="u-dt")

                # Extract Unix time from the 'data-time' attribute
                unix_time = int(time_tag.get("data-time")) if time_tag else None

                # Skip the thread if its Unix time is less than the input Unix time
                if unix_time and unix_time < input_unix_time:
                    continue

                # Extract the link to the thread
                link = topic.get("href")
                full_link = (
                    f"https://www.resetera.com{link}" if link else "Link: Not Found"
                )

                # Create a dict with the thread's details and add it to the list
                thread_dict = {
                    "title": topic.text.strip(),
                    "time": unix_time,
                    "link": full_link,
                }
                logging.info(thread_dict)
                threads_list.append(thread_dict)
    else:
        logging.error(
            f"Failed to retrieve page {page}. Status code: {response.status_code}"
        )

# Write the current Unix time to the timestamp file
try:
    with open(timestamp_file, "w") as file:
        file.write(str(int(time.time())))
    logging.info(f"Successfully wrote to {timestamp_file}")
except Exception as e:
    logging.error(f"Failed to write to {timestamp_file}. Error: {e}")

# Convert the list of dicts to a JSON string
try:
    threads_json = json.dumps(threads_list, indent=4)
    logging.info("Successfully converted threads list to JSON")
except json.JSONDecodeError as e:
    logging.error(f"Failed to convert threads list to JSON. Error: {e}")

# Bluesky init
now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

try:
    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": blue_login, "password": blue_password},
    )
    resp.raise_for_status()
    session = resp.json()
except requests.RequestException as e:
    logging.error(f"Failed to create BlueSky session. Error: {e}")
except json.JSONDecodeError as e:
    logging.error(f"Failed to decode BlueSky session response. Error: {e}")

# Loop through the threads_list (old -> new) and post each thread title as a link to BlueSky
for thread in reversed(threads_list):
    link = thread["link"]
    title = thread["title"]

    # Fetch card contents
    try:
        resp = requests.get(link)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logging.error(f"Failed to fetch card contents. Error: {e}")

    title_tag = soup.find("meta", property="og:title")["content"]
    try:
        description_tag = soup.find("meta", property="og:description")["content"]
    except TypeError:
        description_tag = "No description"
    except KeyError:
        description_tag = "No description"

    # Upload card image
    # Find the first <img> tag with src starting with "/data/avatar/"
    img_tag = soup.find("img", src=lambda x: x and x.startswith("/data/avatar/"))
    # If found, construct the full URL, else use the default meta image. Use the default meta image if the specific <img> tag is not found.
    if img_tag:
        image_url = f"https://www.resetera.com{img_tag['src']}"
    else:
        image_url = soup.find("meta", property="og:image")["content"]
    try:
        resp = requests.get(image_url)
        resp.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch card image. Error: {e}")
    blob_resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.repo.uploadBlob",
        headers={
            "Content-Type": "image/png",
            "Authorization": "Bearer " + session["accessJwt"],
        },
        data=resp.content,
    )
    blob_resp.raise_for_status()
    thumb = blob_resp.json()["blob"]

    # 300 character limit
    if len(title) > 300:
        title = title[: max_title_length - 3] + "..."  # Truncate and add ellipsis

    byte_end = len(title.encode("utf-8"))

    post = {
        "$type": "app.bsky.feed.post",
        "text": "",
        "createdAt": now,
        "langs": ["en-US"],
        # Card
        "embed": {
            "$type": "app.bsky.embed.external",
            "external": {
                "uri": link,
                "title": title_tag,
                "description": description_tag,
                "thumb": thumb,
            },
        },
    }

    try:
        resp = requests.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": "Bearer " + session["accessJwt"]},
            json={
                "repo": session["did"],
                "collection": "app.bsky.feed.post",
                "record": post,
            },
        )
        logging.info(json.dumps(resp.json(), indent=2))
        resp.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to post to BlueSky. Error: {e}")
        continue
    except json.JSONDecodeError as e:
        logging.error(f"Failed to decode BlueSky post response. Error: {e}")
        continue
