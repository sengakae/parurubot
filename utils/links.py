import io
import logging
import re

import aiohttp
from PIL import Image

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024


class CustomClientRequest(aiohttp.ClientRequest):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_field_size = 32768


def extract_links(text: str):
    """Find all http/https URLs in text."""
    if not text:
        return []
    url_pattern = r"(https?://\S+)"
    return re.findall(url_pattern, text)


async def download_image_from_url(url: str):
    """Download an image from a URL if it is an actual image."""
    try:
        async with aiohttp.ClientSession(request_class=CustomClientRequest) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.info(f"Failed to fetch {url}, status {resp.status}")
                    return None

                content_type = resp.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    logger.info(
                        f"URL is not an image: {url} (Content-Type: {content_type})"
                    )
                    return None
                # the e2-micro has ~200 MB free; one huge "image" must not take it all
                if (resp.content_length or 0) > MAX_IMAGE_BYTES:
                    logger.info(f"Image too large, skipping: {url} ({resp.content_length} bytes)")
                    return None

                data = await resp.read()
                image = Image.open(io.BytesIO(data))
                return image

    except Exception as e:
        logger.exception(f"Error downloading image from {url}: {e}")
        return None


def image_urls(content, attachments=()):
    """URLs that may hold a message's images: its image attachments, plus every link
    (a link only turns out to be an image when it is downloaded)."""
    urls = [
        a.url for a in attachments
        if a.content_type is None or a.content_type.startswith("image/")
    ]
    return urls + extract_links(content)


async def download_images(urls):
    """Download the URLs that are images; the rest are skipped."""
    images = []
    for url in urls:
        image = await download_image_from_url(url)
        if image:
            images.append(image)
    return images


def extract_youtube_urls(text: str):
    """Extract YouTube URLs from text and return (urls, stripped_text)."""
    if not text:
        return [], text

    youtube_pattern = r"(https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+|youtube\.com/embed/[\w-]+|youtube\.com/v/[\w-]+))"

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
