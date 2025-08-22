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


async def collect_images_from_message(message):
    """Collect all images from a single message"""
    images = []
    
    if message.attachments:
        images.extend(await collect_images_from_attachments(message.attachments))
    
    images.extend(await collect_images_from_links(message.content))
    
    return images


def extract_youtube_urls(text):
    """Extract YouTube URLs from text"""
    if not text or 'youtube' not in text.lower() and 'youtu.be' not in text.lower():
        return []
    
    youtube_pattern = r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]+)'
    matches = re.findall(youtube_pattern, text)
    return [f"https://www.youtube.com/watch?v={match}" for match in matches]