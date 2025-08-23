import io
import re

import aiohttp
from PIL import Image


def extract_links(text: str):
    """Find all http/https URLs in text."""
    if not text:
        return []
    url_pattern = r'(https?://\S+)'
    return re.findall(url_pattern, text)


async def download_image_from_url(url: str):
    """Download an image from a URL if it is an actual image."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"Failed to fetch {url}, status {resp.status}")
                    return None

                content_type = resp.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    print(f"URL is not an image: {url} (Content-Type: {content_type})")
                    return None

                data = await resp.read()
                image = Image.open(io.BytesIO(data))
                return image

    except Exception as e:
        print(f"Error downloading image from {url}: {e}")
        return None


async def collect_images_from_attachments(attachments):
    """Helper function to collect images from attachments"""
    images = []
    for attachment in attachments:
        image = await download_image_from_url(attachment.url)
        if image:
            images.append(image)
    return images


async def collect_images_from_links(content):
    """Helper function to collect images from links in content"""
    images = []
    links = extract_links(content)
    for link in links:
        image = await download_image_from_url(link)
        if image:
            images.append(image)
    return images


async def collect_images_from_message(content, attachments=None):
    """Collect all images from a single message"""
    images = []
    
    if attachments:
        images.extend(await collect_images_from_attachments(attachments))
    
    images.extend(await collect_images_from_links(content))
    
    return images


def extract_youtube_urls(text: str):
    """Extract YouTube URLs from text and return (urls, stripped_text)."""
    if not text:
        return [], text

    youtube_pattern = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+|youtube\.com/embed/[\w-]+|youtube\.com/v/[\w-]+))'

    matches = re.findall(youtube_pattern, text)
    urls = []

    for match in matches:
        video_id = None
        if "youtu.be/" in match:
            video_id = match.split("/")[-1]
        elif "watch?v=" in match:
            video_id = match.split("watch?v=")[-1].split("&")[0]
        elif "embed/" in match:
            video_id = match.split("embed/")[-1]
        elif "/v/" in match:
            video_id = match.split("/v/")[-1]

        if video_id:
            urls.append(f"https://www.youtube.com/watch?v={video_id}")

    stripped_text = re.sub(youtube_pattern, "", text).strip()

    return urls, stripped_text
