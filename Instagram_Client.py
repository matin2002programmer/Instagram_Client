import json
import os
import random
import re
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx


class MediaType(Enum):
    """Enum for different media types"""
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL = "carousel"
    REEL = "reel"
    STORY = "story"
    HIGHLIGHT = "highlight"


@dataclass
class MediaInfo:
    """Data class to store media information"""
    url: str
    media_type: MediaType
    caption: str
    shortcode: str
    username: Optional[str] = None
    index: Optional[int] = None
    timestamp: Optional[int] = None

    @property
    def extension(self) -> str:
        return ".mp4" if self.media_type in [MediaType.VIDEO, MediaType.REEL] else ".jpg"

    @property
    def filename(self) -> str:
        safe_caption = re.sub(r'[\\/:*?"<>|]', '_', '_'.join(self.caption.split()[:4]) or 'no_caption')

        if self.media_type == MediaType.STORY:
            timestamp_str = str(self.timestamp) if self.timestamp else self.shortcode
            return f"{self.username}_{timestamp_str}{self.extension}"
        elif self.media_type == MediaType.HIGHLIGHT:
            return f"{self.username}_highlight_{self.shortcode}_{self.index or 1}{self.extension}"
        elif self.index:
            return f"{safe_caption}_{self.shortcode}_{self.index}{self.extension}"
        return f"{safe_caption}_{self.shortcode}{self.extension}"


@dataclass
class UserInfo:
    """Data class for user information"""
    user_id: str
    username: str
    full_name: str
    is_private: bool
    profile_pic_url: Optional[str] = None
    post_count: int = 0
    follower_count: int = 0
    following_count: int = 0


class InstagramClient:
    """
    Instagram Media Downloader & Uploader

    A comprehensive class for downloading media from Instagram including:
    - Posts (single and carousel)
    - Reels
    - Stories (now supports single story via URL)
    - Highlights
    - Complete user profiles

    New update:
        Now you also can:
        - Upload post
        - Upload reel
        - Comment on first post of user (x)
        - Comment on the post you want using link of post
    """

    # Class constants
    BASE_URL = "https://www.instagram.com"
    I_BASE_URL = "https://i.instagram.com"  # Added for story endpoints
    LOGIN_URL = "https://www.instagram.com/api/v1/web/accounts/login/ajax/"
    API_URL = f"{BASE_URL}/api/v1"
    GRAPHQL_URL = f"{BASE_URL}/graphql/query"
    DEFAULT_TIMEOUT = 15.0
    COOKIES_FILE = "cookies.json"

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    ]

    # Known working doc_ids for different operations (updated where needed; stories now use REST endpoint)
    DOC_IDS = {
        'post_primary': "8845758582119845",
        'post_fallbacks': [
            "7950326061742207",
            "9830740690327183",
            "9935000046557399",
            "10015901848480474",
            "23907016675582737",
            "24141963108832236",
            "24319041294395440",
            "29588494114099064",
            "29789987647283145",
            "29645355751775862"
        ],
        'user_posts': "9310670392322965",
        'user_posts_fallbacks': [
            "17862778007156914",
            "17880305679164675",
            "17885113105037631"
        ],
        'stories': "25317500907894419",  # Kept for fallback, but primary is now REST
        'stories_fallbacks': [
            "17890626976041463",
            "17859317138994014",
            "17945255025108868"
        ],
        'highlights': "17864450716183058",
        'highlights_fallbacks': [
            "17965172502067288",
            "17913930445236500",
            "17862894953138603"
        ],
        'highlight_items': "25147404345163462",
        'user_info': "23859202738984123",
    }

    # Bundle names to check for doc_id
    BUNDLE_NAMES = ["consumer", "postpagecontainer", "profilepagecontainer", "storiespage"]

    def __init__(self, download_dir: str = "downloads", timeout: float = DEFAULT_TIMEOUT,
                 cookies: Optional[Dict[str, str]] = None):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)

        self.user_agent = random.choice(self.USER_AGENTS)  # Rotate UA for better evasion
        self.session = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=timeout
        )
        self.app_id: str = "936619743392459"

        self.csrf_token = None
        self.lsd_token = None

        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)

        self.user_agent = random.choice(self.USER_AGENTS)
        self.session = httpx.Client(headers={"User-Agent": self.user_agent}, timeout=timeout)
        self.session_id = None

        # For auto re-login
        self._username = None
        self._password = None

        if cookies:
            for k, v in cookies.items():
                self.session.cookies.set(k, v, domain=".instagram.com")
            self.csrf_token = cookies.get("csrftoken")

        # Try auto-load cookies
        self._load_cookies()

        if cookies:
            for k, v in cookies.items():
                self.session.cookies.set(k, v, domain=".instagram.com")
            self.csrf_token = cookies.get("csrftoken")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close the session"""
        self.close()

    def close(self):
        """Close the HTTP session"""
        if self.session:
            self.session.close()

    # ---------- REQUEST WRAPPER ---------- #
    def _request(self, method: str, url: str, **kwargs):
        try:
            resp = self.session.request(method, url, **kwargs)
        except socket.gaierror as e:
            print(f"[!] Network error: {e}. Check internet/DNS.")
            return None  # Or retry logic

        # Check if this is a comment request and if we should skip retry to prevent duplicates
        is_comment_request = "/comments/" in url and "/add/" in url and method == "POST"

        if resp.status_code in (401, 403) or "login_required" in resp.text:
            if self._username and self._password and not is_comment_request:
                print("[!] Session expired. Re-logging in...")
                if self._do_login(self._username, self._password):
                    resp = self.session.request(method, url, **kwargs)
            else:
                if is_comment_request:
                    print("[!] Session expired during comment - skipping retry to prevent duplicate")
                else:
                    print("[!] Session expired and no credentials available.")
        return resp

    # ---------- COOKIES ---------- #
    def _save_cookies(self):
        try:
            with open(self.COOKIES_FILE, "w") as f:
                json.dump(dict(self.session.cookies), f)
            print(f"[✓] Cookies saved to {self.COOKIES_FILE}")
        except Exception as e:
            print(f"[!] Failed to save cookies: {e}")

    def _load_cookies(self):
        if os.path.exists(self.COOKIES_FILE):
            try:
                with open(self.COOKIES_FILE, "r") as f:
                    cookies = json.load(f)
                for k, v in cookies.items():
                    self.session.cookies.set(k, v, domain=".instagram.com")
                self.csrf_token = cookies.get("csrftoken")
                self.session_id = cookies.get("sessionid")
                print(f"[✓] Loaded cookies from {self.COOKIES_FILE}")
                return True
            except Exception as e:
                print(f"[!] Failed to load cookies: {e}")
        return False

    # ---------- LOGIN ---------- #
    def login(self, username: str, password: str, force: bool = False) -> bool:
        self._username = username
        self._password = password
        if not force and self._load_cookies():
            print("[*] Using existing cookies")
            return True
        return self._do_login(username, password)

    def _do_login(self, username: str, password: str) -> bool:
        try:
            self._fetch_tokens()
            headers = self._prepare_headers(referer=f"{self.BASE_URL}/accounts/login/")
            payload = {
                "username": username,
                "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}",
                "queryParams": {},
                "optIntoOneTap": "false"
            }
            resp = self._request("POST", self.LOGIN_URL, data=payload, headers=headers)
            data = resp.json()
            if resp.status_code == 200 and data.get("authenticated"):
                self.csrf_token = self.session.cookies.get("csrftoken")
                self.session_id = self.session.cookies.get("sessionid")
                print(f"[✓] Logged in as @{username}")
                self._save_cookies()
                return True
            elif data.get("message") == "checkpoint_required":
                print("[!] Challenge required.")
                return self._handle_checkpoint(data.get("checkpoint_url"))
            else:
                print(f"[!] Login failed: {data}")
                return False
        except Exception as e:
            print(f"[!] Error during login: {e}")
            return False

    # ---------- HANDLE CHALLENGE ---------- #
    def _handle_checkpoint(self, checkpoint_url: str) -> bool:
        url = f"{self.BASE_URL}{checkpoint_url}"
        headers = self._prepare_headers()
        resp = self._request("GET", url, headers=headers)
        try:
            data = resp.json()
        except Exception:
            print("[!] Challenge response not JSON")
            return False
        step_name = data.get("step_name")
        if step_name == "select_verify_method":
            methods = data.get('step_data', {})
            print(f"[*] Available methods: Email (1), SMS (0)")
            choice = input("Enter choice (0 or 1): ")
            self._request("POST", url, headers=headers, data={"choice": choice})
            print("[*] Verification code sent.")
            code = input("Enter the code: ")
            resp = self._request("POST", url, headers=headers, data={"security_code": code})
            if resp.status_code == 200 and "ok" in resp.text.lower():
                print("[✓] Challenge solved.")
                self._save_cookies()
                return True
        print("[!] Could not solve challenge. Try manual login in browser.")
        return False

    def _extract_shortcode(self, url: str) -> str:
        """Extract shortcode from Instagram URL"""
        parsed = urlparse(url)
        if parsed.netloc not in ['www.instagram.com', 'instagram.com']:
            raise ValueError(f"Invalid Instagram URL: {url}")

        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) < 2:
            raise ValueError(f"Cannot extract shortcode from URL: {url}")

        if path_parts[0] in ['p', 'reel', 'tv']:
            return path_parts[1].split('?')[0]

        raise ValueError(f"Unsupported URL pattern: {url}")

    def _extract_username(self, url: str) -> str:
        """Extract username from Instagram URL"""
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')

        if path_parts[0] == 'stories' and len(path_parts) > 1:
            return path_parts[1]
        elif path_parts[0] not in ['p', 'reel', 'tv', 'explore']:
            return path_parts[0]

        raise ValueError(f"Cannot extract username from URL: {url}")

    def _extract_story_pk(self, url: str) -> Optional[str]:
        """Extract story_pk from story URL if present"""
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        if path_parts[0] == 'stories' and len(path_parts) == 3:
            return path_parts[2]
        return None

    # ---------- TOKENS ---------- #
    def _fetch_tokens(self) -> bool:
        try:
            response = self._request("GET", self.BASE_URL)
            lsd_match = re.search(r'"lsd":"(.*?)"', response.text)
            if lsd_match:
                self.lsd_token = lsd_match.group(1)
            csrf_match = re.search(r'"csrf_token":"(.*?)"', response.text)
            if csrf_match:
                self.csrf_token = csrf_match.group(1)
            if 'sessionid' in response.cookies:
                self.session_id = response.cookies['sessionid']
            return True
        except Exception:
            return False

    # ---------- HEADERS ---------- #
    def _prepare_headers(self, referer: str = None) -> Dict[str, str]:
        headers = {
            "user-agent": self.user_agent,
            "x-ig-app-id": self.app_id,
            "referer": referer or f"{self.BASE_URL}/",
            "x-ig-www-claim": "0",
            "x-asbd-id": "198387",  # Add this (from browser inspection)
            "x-instagram-ajax": "1000000000",  # Randomize or fetch from page
            "x-requested-with": "XMLHttpRequest",  # Mimic AJAX
            "origin": "https://www.instagram.com",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded"
        }
        if self.csrf_token:
            headers["x-csrftoken"] = self.csrf_token
        if self.lsd_token:
            headers["x-fb-lsd"] = self.lsd_token  # Or 'x-lsd' in some cases
        return headers

    def _get_user_info(self, username: str) -> Optional[UserInfo]:
        """Get user information via Instagram API"""
        try:
            print(f"[*] Fetching user info for @{username}...")

            url = f"{self.API_URL}/users/web_profile_info/?username={username}"
            headers = self._prepare_headers(referer=f"{self.BASE_URL}/{username}/")

            response = self.session.get(url, headers=headers)

            if response.status_code == 200:
                data = response.json()
                user = data.get("data", {}).get("user")
                if not user:
                    return None

                return UserInfo(
                    user_id=user.get("id", ""),
                    username=user.get("username", username),
                    full_name=user.get("full_name", ""),
                    is_private=user.get("is_private", False),
                    profile_pic_url=user.get("profile_pic_url_hd"),
                    post_count=user.get("edge_owner_to_timeline_media", {}).get("count", 0),
                    follower_count=user.get("edge_followed_by", {}).get("count", 0),
                    following_count=user.get("edge_follow", {}).get("count", 0),
                )

            else:
                print(f"[!] Failed to fetch user info: {response.status_code}")
                return None

        except Exception as e:
            print(f"[!] Error fetching user info: {e}")
            return None

    def _fetch_single_story(self, story_pk: str, username: str) -> List[MediaInfo]:
        """Fetch a single story using media/info endpoint"""
        media_list = []
        try:
            print(f"[*] Fetching single story {story_pk} for @{username}...")

            headers = self._prepare_headers(referer=f"{self.BASE_URL}/stories/{username}/{story_pk}/")

            url = f"{self.I_BASE_URL}/api/v1/media/{story_pk}/info/"
            response = self.session.get(url, headers=headers)

            if response.status_code == 200:
                data = response.json()
                items = data.get('items', [])
                if items:
                    item = items[0]  # Single item
                    is_video = False if item.get('video_duration') is None else True
                    if is_video:
                        video_versions = item.get('video_versions', [])
                        if video_versions:
                            media_url = max(video_versions, key=lambda x: x.get('height', 0))['url']
                        else:
                            media_url = ''
                    else:
                        image_versions = item.get('image_versions2', {}).get('candidates', [])
                        if image_versions:
                            media_url = max(image_versions, key=lambda x: x.get('height', 0))['url']
                        else:
                            media_url = ''

                    if media_url:
                        media_list.append(MediaInfo(
                            url=media_url,
                            media_type=MediaType.STORY if not is_video else MediaType.VIDEO,
                            caption="Story",
                            shortcode=item.get('pk', story_pk),
                            username=username,
                            timestamp=item.get('taken_at'),
                            index=1
                        ))
                    print(f"[✓] Found 1 story item")

            else:
                print(f"[!] Failed to fetch single story: {response.status_code}")

        except Exception as e:
            print(f"[!] Error fetching single story: {e}")

        return media_list

    def _fetch_stories(self, user_id: str, username: str) -> List[MediaInfo]:
        """Fetch user stories using correct REST endpoint"""
        media_list = []

        try:
            print(f"[*] Fetching stories for @{username}...")

            headers = self._prepare_headers(referer=f"{self.BASE_URL}/stories/{username}/")

            # Correct endpoint for user stories
            url = f"{self.I_BASE_URL}/api/v1/feed/reels_media/?reel_ids={user_id}"
            response = self.session.get(url, headers=headers)

            if response.status_code == 200:
                data = response.json()
                reels = data.get('reels', {})
                if user_id in reels:
                    items = reels[user_id].get('items', [])

                    print(f"[✓] Found {len(items)} stories")

                    for idx, item in enumerate(items, 1):
                        is_video = item.get('media_type', 1) == 2  # 1=image, 2=video
                        if is_video:
                            video_versions = item.get('video_versions', [])
                            if video_versions:
                                media_url = max(video_versions, key=lambda x: x.get('height', 0))['url']
                            else:
                                media_url = ''
                        else:
                            image_versions = item.get('image_versions2', {}).get('candidates', [])
                            if image_versions:
                                media_url = max(image_versions, key=lambda x: x.get('height', 0))['url']
                            else:
                                media_url = ''

                        if media_url:
                            media_list.append(MediaInfo(
                                url=media_url,
                                media_type=MediaType.STORY if not is_video else MediaType.VIDEO,
                                caption=f"Story {idx}",
                                shortcode=item.get('pk', f'story_{idx}'),
                                username=username,
                                timestamp=item.get('taken_at'),
                                index=idx
                            ))

            else:
                print(f"[!] Failed to fetch stories: {response.status_code}")
                # Fallback to original GraphQL if needed (commented out for now)
                # variables = {"reel_ids": [user_id], ...}  # as in original
                # ... try GraphQL ...

        except Exception as e:
            print(f"[!] Error fetching stories: {e}")

        return media_list

    def _fetch_highlights(self, user_id: str, username: str) -> Dict[str, List[MediaInfo]]:
        """Fetch user highlights"""
        highlights_dict = {}

        try:
            print(f"[*] Fetching highlights for @{username}...")

            # First, get the list of highlights
            variables = {
                "user_id": user_id,
                "include_chaining": True,
                "include_reel": True,
                "include_suggested_users": False,
                "include_logged_out_extras": False,
                "include_highlight_reels": True,
                "include_live_status": True
            }

            headers = self._prepare_headers(referer=f"{self.BASE_URL}/{username}/")

            # Try to get highlights list
            doc_ids = [self.DOC_IDS['highlights']] + self.DOC_IDS['highlights_fallbacks']

            highlight_ids = []

            for doc_id in doc_ids:
                try:
                    variables_json = json.dumps(variables, separators=(',', ':'))
                    url = f"{self.GRAPHQL_URL}?doc_id={doc_id}&variables={quote(variables_json)}"

                    response = self.session.get(url, headers=headers)

                    if response.status_code == 200:
                        data = response.json()

                        if 'data' in data and 'user' in data['data']:
                            user_data = data['data']['user']
                            highlights = user_data.get('edge_highlight_reels', {}).get('edges', [])

                            for highlight in highlights:
                                node = highlight['node']
                                highlight_ids.append({
                                    'id': node['id'],
                                    'title': node.get('title', 'Untitled')
                                })

                            if highlight_ids:
                                print(f"[✓] Found {len(highlight_ids)} highlights")
                                break

                except Exception as e:
                    continue

            # Now fetch each highlight's content
            for highlight_info in highlight_ids:
                highlight_id = highlight_info['id']
                highlight_title = highlight_info['title']

                print(f"[*] Fetching highlight: {highlight_title}")

                # Fetch highlight items
                variables = {
                    "reel_ids": [],
                    "tag_names": [],
                    "location_ids": [],
                    "highlight_reel_ids": [highlight_id],
                    "precomposed_overlay": False,
                    "show_story_viewer_list": False,
                    "story_viewer_fetch_count": 0,
                    "story_viewer_cursor": "",
                    "stories_video_dash_manifest": False
                }

                variables_json = json.dumps(variables, separators=(',', ':'))
                url = f"{self.GRAPHQL_URL}?doc_id={self.DOC_IDS['highlight_items']}&variables={quote(variables_json)}"

                try:
                    response = self.session.get(url, headers=headers)

                    if response.status_code == 200:
                        data = response.json()

                        if 'data' in data and 'reels_media' in data['data']:
                            reels = data['data']['reels_media']
                            if reels and len(reels) > 0:
                                reel = reels[0]
                                items = reel.get('items', [])

                                media_list = []

                                for idx, item in enumerate(items, 1):
                                    is_video = item.get('is_video', False)

                                    if is_video:
                                        video_resources = item.get('video_resources', [])
                                        if video_resources:
                                            media_url = max(video_resources, key=lambda x: x.get('config_height', 0))[
                                                'src']
                                        else:
                                            media_url = item.get('video_url', '')
                                    else:
                                        display_resources = item.get('display_resources', [])
                                        if display_resources:
                                            media_url = max(display_resources, key=lambda x: x.get('config_height', 0))[
                                                'src']
                                        else:
                                            media_url = item.get('display_url', '')

                                    if media_url:
                                        media_list.append(MediaInfo(
                                            url=media_url,
                                            media_type=MediaType.HIGHLIGHT,
                                            caption=highlight_title,
                                            shortcode=f"{highlight_id}_{idx}",
                                            username=username,
                                            index=idx
                                        ))

                                if media_list:
                                    highlights_dict[highlight_title] = media_list

                except Exception as e:
                    print(f"[!] Error fetching highlight {highlight_title}: {e}")

        except Exception as e:
            print(f"[!] Error fetching highlights: {e}")

        return highlights_dict

    from typing import List, Dict, Optional, Tuple

    # Assuming MediaInfo and MediaType are defined elsewhere, e.g.:
    from dataclasses import dataclass
    from enum import Enum

    class MediaType(Enum):
        IMAGE = "image"
        VIDEO = "video"

    @dataclass
    class MediaInfo:
        url: str
        media_type: MediaType
        caption: str = ""
        shortcode: str = ""
        index: int = 0

        @property
        def filename(self) -> str:
            ext = "mp4" if self.media_type == MediaType.VIDEO else "jpg"
            if self.index > 0:
                return f"{self.shortcode}_{self.index}.{ext}"
            return f"{self.shortcode}.{ext}"

    # The fixed code starts here

    def _extract_shortcode(self, url: str) -> str:
        """Extract shortcode from post or reel URL"""
        match = re.match(r"https?://www\.instagram\.com/(p|reel|reels)/([^/]+)/?", url.strip().rstrip(','))
        if match:
            return match.group(2)
        else:
            raise ValueError(f"Unsupported URL pattern: {url}")

    def _fetch_user_posts(self, user_id: str, username: str, max_posts: int = 50) -> List[str]:
        """Fetch user's post URLs"""
        post_urls = []

        try:
            print(f"[*] Fetching posts for @{username}...")

            variables = {
                "after": None,
                "before": None,
                "data": {
                    "count": min(max_posts, 50),
                    "include_reel_media_seen_timestamp": True,
                    "include_relationship_info": True,
                    "latest_besties_reel_media": True,
                    "latest_reel_media": True
                },
                "first": min(max_posts, 50),
                "last": None,
                "username": username,
                "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
                "__relay_internal__pv__PolarisShareSheetV3relayprovider": True,
            }

            headers = self._prepare_headers(referer=f"{self.BASE_URL}/{username}/")

            doc_ids = [self.DOC_IDS['user_posts']] + self.DOC_IDS['user_posts_fallbacks']

            has_next = True
            total_fetched = 0

            while has_next and total_fetched < max_posts:
                fetched_this_page = 0  # Track progress per iteration
                for doc_id in doc_ids:
                    try:
                        variables_json = json.dumps(variables, separators=(',', ':'))
                        url = f"{self.GRAPHQL_URL}?doc_id={doc_id}&variables={quote(variables_json)}"

                        response = self.session.get(url, headers=headers)

                        print(f"[*] Response status for doc_id {doc_id}: {response.status_code}")  # Debug

                        if response.status_code == 200:
                            data = response.json()

                            if 'data' in data and 'xdt_api__v1__feed__user_timeline_graphql_connection' in data['data']:
                                media = data['data']['xdt_api__v1__feed__user_timeline_graphql_connection']
                                edges = media.get('edges', [])

                                for edge in edges:
                                    node = edge['node']
                                    shortcode = node.get('code') or node.get('shortcode')  # Handle both formats
                                    if shortcode:
                                        product_type = node.get('product_type', 'feed')
                                        if product_type == 'clips':
                                            post_url = f"{self.BASE_URL}/reels/{shortcode}/"
                                        else:
                                            post_url = f"{self.BASE_URL}/p/{shortcode}/"
                                        post_urls.append(post_url)
                                        total_fetched += 1
                                        fetched_this_page += 1

                                        if total_fetched >= max_posts:
                                            has_next = False
                                            break

                                # Check for next page
                                page_info = media.get('page_info', {})
                                has_next = page_info.get('has_next_page', False) and total_fetched < max_posts

                                if has_next:
                                    variables['after'] = page_info.get('end_cursor')
                                    time.sleep(random.uniform(2.0, 4.0))  # Rate limiting

                                break  # Successful doc_id, exit inner loop

                    except Exception as e:
                        print(f"[!] Error with doc_id {doc_id}: {e}")
                        continue

                # If no posts fetched this page (failed all doc_ids), prevent infinite loop
                if fetched_this_page == 0:
                    print(f"[!] No progress fetching posts; possible block or invalid doc_id. Stopping.")
                    has_next = False

            print(f"[✓] Found {len(post_urls)} posts")

        except Exception as e:
            print(f"[!] Error fetching user posts: {e}")

        return post_urls

    def _fetch_post_data(self, shortcode: str, doc_id: str, headers: Dict[str, str]) -> Optional[Dict]:
        """Fetch post data from Instagram GraphQL API"""
        variables = json.dumps({'shortcode': shortcode}, separators=(',', ':'))
        variables_quoted = quote(variables)

        url = f"{self.GRAPHQL_URL}?doc_id={doc_id}&variables={variables_quoted}"

        try:
            response = self.session.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and "xdt_shortcode_media" in data["data"]:
                    return data
        except Exception as e:
            print(f"[!] Error fetching with doc_id {doc_id}: {e}")

        return None

    def _extract_media_info(self, data: Dict) -> List[MediaInfo]:
        """Extract media information from API response"""
        media_data = data["data"]["xdt_shortcode_media"]
        shortcode = media_data["shortcode"]

        # Extract caption
        caption_edges = media_data.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""

        media_list = []
        # Check if it's a carousel/sidecar
        if "edge_sidecar_to_children" in media_data:
            for idx, child in enumerate(media_data["edge_sidecar_to_children"]["edges"], start=1):
                node = child["node"]
                is_video = node.get("is_video", False)
                media_url = node["video_url"] if is_video else node["display_url"]
                media_type = MediaType.VIDEO if is_video else MediaType.IMAGE

                media_list.append(MediaInfo(
                    url=media_url,
                    media_type=media_type,
                    caption=caption,
                    shortcode=shortcode,
                    index=idx
                ))
        else:
            # Single media
            is_video = media_data.get("is_video", False)
            media_url = media_data["video_url"] if is_video else media_data["display_url"]
            media_type = MediaType.VIDEO if is_video else MediaType.IMAGE

            media_list.append(MediaInfo(
                url=media_url,
                media_type=media_type,
                caption=caption,
                shortcode=shortcode
            ))

        return media_list

    def _download_file(self, media_info: MediaInfo, subfolder: str = None) -> bool:
        """Download a single media file"""
        # Create subfolder if specified
        if subfolder:
            folder = self.download_dir / subfolder
            folder.mkdir(exist_ok=True)
        else:
            folder = self.download_dir

        filepath = folder / media_info.filename

        try:
            print(f"[*] Downloading: {media_info.filename}")

            with self.session.stream("GET", media_info.url) as response:
                response.raise_for_status()

                # Get file size if available
                total_size = int(response.headers.get('content-length', 0))

                with open(filepath, "wb") as f:
                    downloaded = 0
                    for chunk in response.iter_bytes():
                        f.write(chunk)
                        downloaded += len(chunk)

                        # Progress indicator
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            print(f"\r[*] Progress: {progress:.1f}%", end="")

            print(f"\n[✓] Saved: {filepath}")
            return True

        except Exception as e:
            print(f"\n[!] Failed to download {media_info.filename}: {e}")
            return False

    def download_post(self, url: str, caption: bool = False) -> Tuple[bool, List[dict[str]] or List[str]]:
        """Download a single post"""
        downloaded_files = []

        try:
            # Extract shortcode
            shortcode = self._extract_shortcode(url)
            print(f"[*] Processing post: {shortcode}")

            # Get tokens
            self._fetch_tokens()

            # Prepare headers
            headers = self._prepare_headers(url)

            # Random delay to avoid rate limiting
            delay = random.uniform(1.5, 3.5)
            print(f"[*] Waiting {delay:.1f}s...")
            time.sleep(delay)

            # Try to fetch post data
            print("[*] Fetching post data...")

            # Try primary doc_id first
            doc_id = self.DOC_IDS['post_primary']
            data = self._fetch_post_data(shortcode, doc_id, headers)

            # Try fallback doc_ids if primary fails
            if not data:
                print("[!] Primary doc_id failed, trying fallbacks...")
                for fallback_id in self.DOC_IDS['post_fallbacks']:
                    print(f"[*] Trying fallback: {fallback_id}")
                    time.sleep(random.uniform(1.0, 2.5))

                    data = self._fetch_post_data(shortcode, fallback_id, headers)
                    if data:
                        print(f"[✓] Working doc_id found: {fallback_id}")
                        break

            if not data:
                print("[!] Failed to fetch post data")
                return False, []

            # Extract media information
            media_list = self._extract_media_info(data)

            print(f"[*] Found {len(media_list)} media item(s)")

            # Download each media
            for media_info in media_list:
                if self._download_file(media_info):
                    if caption:
                        downloaded_files.append({"filename": media_info.filename, "caption": media_info.caption,
                                                 "media_type": media_info.media_type})
                    else:
                        downloaded_files.append(media_info.filename)

            print(f"\n[✓] Download complete! {len(downloaded_files)} file(s) saved")
            return True, downloaded_files

        except Exception as e:
            print(f"[!] Error: {e}")
            return False, downloaded_files

    def download_story(self, url: str, info: bool = True) -> Tuple[bool, List[str] or List[dict[str]]]:
        """
        Download a specific story from a URL (e.g., https://www.instagram.com/stories/psg/3702517752195613477/)

        Args:
            url: Direct link to the story

        Returns:
            Tuple of (success, list of downloaded filenames)
            :param info:
        """
        downloaded_files = []

        try:
            # Get tokens
            self._fetch_tokens()

            # Extract username and optional story_pk
            username = self._extract_username(url)
            story_pk = self._extract_story_pk(url)

            # Get user info
            user_info = self._get_user_info(username)
            if not user_info:
                print(f"[!] Could not fetch user info for @{username}")
                return False, []

            if user_info.is_private and not self.session.cookies.get('sessionid'):
                print(f"[!] @{username} is private. Provide session cookies for access.")
                return False, []

            # Fetch stories
            if story_pk:
                stories = self._fetch_single_story(story_pk, username)
            else:
                stories = self._fetch_stories(user_info.user_id, username)

            if not stories:
                print(f"[!] No stories found for @{username}")
                return False, []

            # Filter to specific story_pk if provided (for all fetch)
            if story_pk and not stories:  # If single fetch failed, perhaps fallback
                stories = self._fetch_stories(user_info.user_id, username)
                stories = [s for s in stories if s.shortcode == story_pk]

            if story_pk and not stories:
                print(f"[!] Story with PK {story_pk} not found or expired")
                return False, []

            print(f"[*] Downloading {len(stories)} story item(s)...")

            for story in stories:
                if self._download_file(story):
                    if info:
                        downloaded_files.append({"filename": story.filename, "media_type": story.media_type})
                    else:
                        downloaded_files.append(story.filename)
                time.sleep(random.uniform(0.5, 1.5))  # Rate limiting

            print(f"\n[✓] Downloaded {len(downloaded_files)} story item(s)")
            return True, downloaded_files

        except Exception as e:
            print(f"[!] Error downloading story: {e}")
            return False, downloaded_files

    # Deprecated: download_stories(username) -> Use download_story with URL instead
    def download_stories(self, username: str) -> Tuple[bool, List[str]]:
        print("[!] Deprecated: Provide a full story URL instead (e.g., https://www.instagram.com/stories/{username}/)")
        url = f"{self.BASE_URL}/stories/{username}/"
        return self.download_story(url)

    def download_highlights(self, username: str) -> Tuple[bool, Dict[str, List[str]]]:
        """
        Download highlights from a user

        Args:
            username: Instagram username

        Returns:
            Tuple of (success, dict of {highlight_title: [filenames]})
        """
        downloaded_files = {}

        try:
            # Get tokens
            self._fetch_tokens()

            # Get user info
            user_info = self._get_user_info(username)
            if not user_info:
                print(f"[!] Could not fetch user info for @{username}")
                return False, {}

            if user_info.is_private:
                print(f"[!] @{username} is a private account. Cannot download highlights.")
                return False, {}

            # Fetch highlights
            highlights = self._fetch_highlights(user_info.user_id, username)

            if not highlights:
                print(f"[!] No highlights found for @{username}")
                return False, {}

            # Create subfolder for highlights
            base_folder = f"highlights_{username}"

            for title, items in highlights.items():
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)
                subfolder = f"{base_folder}/{safe_title}"
                files = []
                print(f"[*] Downloading {len(items)} items from highlight: {title}")

                for media in items:
                    if self._download_file(media, subfolder):
                        files.append(media.filename)
                    time.sleep(random.uniform(0.5, 1.5))  # Rate limiting

                downloaded_files[title] = files

            print(f"\n[✓] Downloaded highlights for @{username}")
            return True, downloaded_files

        except Exception as e:
            print(f"[!] Error downloading highlights: {e}")
            return False, downloaded_files

    def download_user_profile(self, username: str, max_posts: int = 50, include_stories: bool = True,
                              include_highlights: bool = True) -> Tuple[bool, Dict[str, List[str]]]:
        """
        Download a complete user profile:
        - Profile picture
        - Posts
        - Stories (optional)
        - Highlights (optional)

        Args:
            username: Instagram username
            max_posts: Maximum number of posts to download
            include_stories: Whether to download stories
            include_highlights: Whether to download highlights

        Returns:
            Tuple of (success, dict of {section: [filenames]})
        """
        results = {"profile_pic": [], "posts": [], "stories": [], "highlights": []}

        try:
            # Get tokens
            self._fetch_tokens()

            # Get user info
            user_info = self._get_user_info(username)
            if not user_info:
                print(f"[!] Could not fetch user info for @{username}")
                return False, results

            if user_info.is_private:
                print(f"[!] @{username} is a private account. Cannot download full profile.")
                return False, results

            # Download profile picture
            if user_info.profile_pic_url:
                profile_media = MediaInfo(
                    url=user_info.profile_pic_url,
                    media_type=MediaType.IMAGE,
                    caption="profile_picture",
                    shortcode="profile",
                    username=username
                )
                if self._download_file(profile_media, f"profile_{username}"):
                    results["profile_pic"].append(profile_media.filename)

            # Download posts
            post_urls = self._fetch_user_posts(user_info.user_id, username, max_posts=max_posts)
            print(f"[*] Downloading {len(post_urls)} posts...")
            for url in post_urls:
                success, files = self.download_post(url)
                if success:
                    results["posts"].extend(files)
                time.sleep(random.uniform(1.0, 2.5))  # Rate limiting

            # Download stories
            if include_stories:
                success, files = self.download_stories(username)
                if success:
                    results["stories"].extend(files)

            # Download highlights
            if include_highlights:
                success, files_dict = self.download_highlights(username)
                if success:
                    for title, files in files_dict.items():
                        results["highlights"].extend(files)

            print(f"\n[✓] Full profile download complete for @{username}")
            return True, results

        except Exception as e:
            print(f"[!] Error downloading user profile: {e}")
            return False, results

    # New functions added based on research from GitHub, StackOverflow, Medium, etc.
    # Note: Uploading and commenting use private web/mobile APIs, which may change and risk account bans.
    # These implementations are simplified and tested against current (2025) endpoints from community sources.

    def get_media_id(self, shortcode: str) -> Optional[str]:
        """Get media ID from shortcode"""
        try:
            headers = self._prepare_headers(f"{self.BASE_URL}/p/{shortcode}/")
            doc_id = self.DOC_IDS['post_primary']
            data = self._fetch_post_data(shortcode, doc_id, headers)
            if not data:
                for fallback_id in self.DOC_IDS['post_fallbacks']:
                    data = self._fetch_post_data(shortcode, fallback_id, headers)
                    if data:
                        break
            if data:
                return data["data"]["xdt_shortcode_media"]["id"]
            return None
        except Exception as e:
            print(f"[!] Error getting media ID: {e}")
            return None

    def comment_on_post(self, post_url: str, comment: str) -> bool:
        """Comment on a specific post via URL (e.g., https://www.instagram.com/p/DNuTzZFCJfZ/)"""
        try:
            if not self.session.cookies.get('sessionid'):
                print("[!] Must be logged in to comment.")
                return False

            shortcode = self._extract_shortcode(post_url)
            media_id = self.get_media_id(shortcode)
            if not media_id:
                print(f"[!] Could not get media ID for {shortcode}")
                return False

            # Enhanced duplicate prevention
            if not hasattr(self, '_recent_comments'):
                self._recent_comments = {}

            comment_key = f"{media_id}:{comment[:50]}"
            current_time = time.time()

            # Check for recent duplicate
            if comment_key in self._recent_comments:
                time_diff = current_time - self._recent_comments[comment_key]
                if time_diff < 30:  # 30 second cooldown
                    print(f"[!] Duplicate comment prevented (posted {time_diff:.1f}s ago)")
                    return False

            # Mark this comment as being attempted
            self._recent_comments[comment_key] = current_time

            url = f"{self.API_URL}/web/comments/{media_id}/add/"
            payload = {"comment_text": comment}
            headers = self._prepare_headers(referer=post_url)
            headers["content-type"] = "application/x-www-form-urlencoded"

            print(f"[*] Posting comment to {shortcode}...")
            resp = self._request("POST", url, data=payload, headers=headers)

            if not resp:
                # Remove the timestamp since request failed
                del self._recent_comments[comment_key]
                return False

            if resp.status_code == 200:
                try:
                    response_data = resp.json()
                    if response_data.get('status') == 'ok':
                        print(f"[✓] Comment added to {shortcode}")
                        return True
                    else:
                        print(f"[!] Failed to comment: {response_data}")
                        # Remove timestamp since comment failed
                        del self._recent_comments[comment_key]
                        return False
                except:
                    print(f"[!] Invalid JSON response: {resp.text[:200]}")
                    del self._recent_comments[comment_key]
                    return False
            else:
                print(f"[!] Failed to comment: HTTP {resp.status_code}")
                # Remove timestamp since comment failed
                del self._recent_comments[comment_key]
                return False

        except Exception as e:
            print(f"[!] Error commenting: {e}")
            # Clean up on error
            if hasattr(self, '_recent_comments') and 'comment_key' in locals():
                self._recent_comments.pop(comment_key, None)
            return False

    def comment_on_first_post(self, username: str, comment: str) -> bool:
        """Comment on the first (latest) post of a user"""
        try:
            if not self.session.cookies.get('sessionid'):
                print("[!] Must be logged in to comment.")
                return False

            user_info = self._get_user_info(username)
            if not user_info:
                return False

            post_urls = self._fetch_user_posts(user_info.user_id, username, max_posts=1)
            if not post_urls:
                print(f"[!] No posts found for @{username}")
                return False

            return self.comment_on_post(post_urls[0], comment)
        except Exception as e:
            print(f"[!] Error commenting on first post: {e}")
            return False

    def like_post(self, post_url: str) -> bool:
        shortcode = self._extract_shortcode(post_url)
        media_id = self.get_media_id(shortcode)

        url = "https://www.instagram.com/graphql/query"

        payload_dict = {
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "usePolarisLikeMediaLikeMutation",
            "server_timestamps": "true",
            "variables": json.dumps({"media_id": media_id, "container_module": "feed_timeline"}),
            "doc_id": "23951234354462179"
        }

        response = self.session.post(url, headers=self._prepare_headers(), data=payload_dict)

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get("status") == "ok":
                    return True

                # For if find new alternative doc_id
                # else:
                #     fallback_id = "23951234354462179"
                #
                #     print(f"[*] Trying fallback: {fallback_id}")
                #     time.sleep(random.uniform(1.0, 2.5))
                #
                #     payload_dict = {
                #         "fb_api_caller_class": "RelayModern",
                #         "fb_api_req_friendly_name": "usePolarisLikeMediaLikeMutation",
                #         "server_timestamps": "true",
                #         "variables": json.dumps({"media_id": media_id, "container_module": "feed_timeline"}),
                #         "doc_id": fallback_id
                #     }
                #
                #     response = self.session.post(url, headers=self._prepare_headers(), data=payload_dict)
                #
                #     try:
                #         data = response.json()
                #         if data.get("status") == "ok":
                #             return True
                #     except json.JSONDecodeError:
                #         pass

            except json.JSONDecodeError:
                pass

        print(f"Failed to like post: {response.status_code} - {response.text}")
        return False

    def unlike_post(self, post_url: str) -> bool:
        shortcode = self._extract_shortcode(post_url)
        media_id = self.get_media_id(shortcode)

        url = "https://www.instagram.com/graphql/query"
        
        payload_dict = {
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "usePolarisLikeMediaUnlikeMutation",
            "server_timestamps": "true",
            "variables": json.dumps({"media_id": media_id}),
            "doc_id": "9624975597538585"
        }

        response = self.session.post(url, headers=self._prepare_headers(), data=payload_dict)

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get("status") == "ok":
                    return True
            except json.JSONDecodeError:
                pass

        print(f"Failed to unlike post: {response.status_code} - {response.text}")
        return False

    def upload_photo(self, file_path: str, caption: str = "") -> bool:
        """Upload a photo with caption (proper chunked upload)"""
        try:
            if not self.session.cookies.get('sessionid'):
                print("[!] Must be logged in to upload.")
                return False

            if not os.path.exists(file_path):
                print(f"[!] File not found: {file_path}")
                return False

            # Generate proper upload ID and get file info
            upload_id = str(int(time.time() * 1000))

            with open(file_path, 'rb') as f:
                data = f.read()

            file_size = len(data)
            entity_name = f"fb_uploader_{upload_id}"

            # Chunked upload with proper offset headers
            url = f"{self.BASE_URL}/rupload_igphoto/{entity_name}"

            headers = self._prepare_headers()
            headers.update({
                "Content-Type": "application/octet-stream",
                "Content-Length": str(file_size),
                "X-Entity-Name": entity_name,
                "X-Entity-Length": str(file_size),
                "X-Entity-Type": "image/jpeg",
                "Offset": "0",  # Capital O and string format
                "X-Instagram-Rupload-Params": json.dumps({
                    "media_type": 1,
                    "upload_id": upload_id,
                    "upload_media_height": 1080,
                    "upload_media_width": 1080,
                    "xsharing_user_ids": json.dumps([]),
                    "image_compression": json.dumps({
                        "lib_name": "moz",
                        "lib_version": "3.1.m",
                        "quality": "80"
                    }),
                }, separators=(',', ':'))
            })

            print(f"[*] Uploading photo...")
            resp = self._request("POST", url, headers=headers, data=data)

            if not resp:
                print("[!] No response from upload")
                return False

            print(f"[*] Upload response: {resp.status_code}")

            if resp.status_code not in [200, 201]:
                print(f"[!] Upload failed with status {resp.status_code}")
                try:
                    error_data = resp.json()
                    print(f"[!] Error details: {error_data}")
                except:
                    print(f"[!] Response text: {resp.text[:300]}")
                return False

            print("[✓] Photo uploaded, configuring post...")

            # Configure the post
            config_url = f"{self.API_URL}/web/create/configure/"
            config_payload = {
                "upload_id": upload_id,
                "caption": caption,
                "usertags": json.dumps([]),
                "custom_accessibility_caption": "",
                "disable_comments": "0",
                "like_and_view_counts_disabled": "0",
                "source_type": "library",
            }

            config_headers = self._prepare_headers()
            config_headers["Content-Type"] = "application/x-www-form-urlencoded"

            resp = self._request("POST", config_url, data=config_payload, headers=config_headers)
            if resp and resp.status_code == 200:
                try:
                    response_data = resp.json()
                    if "media" in response_data or response_data.get("status") == "ok":
                        print(f"[✓] Photo posted successfully!")
                        return True
                    else:
                        print(f"[!] Configure failed: {response_data}")
                        return False
                except:
                    print(f"[!] Configure response parsing failed: {resp.text[:200]}")
                    return False
            else:
                print(f"[!] Configure failed: {resp.status_code if resp else 'No response'}")
                if resp:
                    print(f"[!] Response: {resp.text[:200]}")
                return False

        except Exception as e:
            print(f"[!] Error uploading photo: {e}")
            return False

    def _upload_photo(self, photo_path: str, upload_id: str) -> Optional[str]:
        """Uploads a photo file for the reel thumbnail."""
        try:
            print(f"[*] Uploading thumbnail: {photo_path}")
            with open(photo_path, 'rb') as f:
                photo_data = f.read()

            photo_size = len(photo_data)

            headers = self._prepare_headers()
            headers.update({
                "Content-Type": "application/octet-stream",
                "X-Entity-Name": f"fb_uploader_{upload_id}",
                "X-Entity-Length": str(photo_size),
                "X-Entity-Type": "image/jpeg",
                "Offset": "0",
                "X-Instagram-Rupload-Params": json.dumps({
                    "media_type": 1,  # 1 for photo, 2 for video
                    "upload_id": upload_id,
                    "is_sidecar": "1",
                })
            })

            url = f"{self.BASE_URL}/rupload_igphoto/fb_uploader_{upload_id}"

            resp = self._request("POST", url, headers=headers, data=photo_data, timeout=60)

            if resp and resp.status_code == 200:
                print("[✓] Thumbnail uploaded successfully")
                return upload_id
            else:
                print(f"[!] Thumbnail upload failed: {resp.status_code if resp else 'No response'}")
                return None

        except Exception as e:
            print(f"[!] Error uploading thumbnail: {e}")
            return None

    # New function for getting image dimensions (similar to video)
    def _get_image_dimensions(self, image_path: str):
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', image_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for stream in data['streams']:
                    if stream['codec_type'] == 'video':
                        return int(stream['width']), int(stream['height'])
        except Exception as e:
            print(f"[!] Error getting image info: {e}")
        return 720, 1280  # Fallback to video dimensions

    # New function for uploading photo (thumbnail), modeled after video upload
    def _upload_photo(self, photo_path: str, upload_id: str) -> Optional[str]:
        try:
            with open(photo_path, 'rb') as f:
                photo_data = f.read()

            photo_size = len(photo_data)
            width, height = self._get_image_dimensions(photo_path)

            entity_name = f"fb_uploader_{upload_id}"
            url = f"{self.BASE_URL}/rupload_igphoto/{entity_name}"

            image_compression = {"quality": 80}

            headers = self._prepare_headers()
            headers.update({
                "Content-Type": "application/octet-stream",
                "Content-Length": str(photo_size),
                "X-Entity-Name": entity_name,
                "X-Entity-Length": str(photo_size),
                "X-Entity-Type": "image/jpeg",
                "Offset": "0",
                "X-Instagram-Rupload-Params": json.dumps({
                    "retry_context": {"num_step_auto_retry": 0, "num_reupload": 0, "num_step_manual_retry": 0},
                    "media_type": 1,
                    "upload_id": upload_id,
                    "xsharing_user_ids": [],
                    "image_compression": json.dumps(image_compression),
                    "is_clips_cover": True,  # Added to indicate it's a cover for clips/reel
                }, separators=(',', ':'))
            })

            print(f"[*] Uploading thumbnail: {photo_path} (using video upload_id: {upload_id})...")
            resp = self._request("POST", url, headers=headers, data=photo_data, timeout=120)

            if resp and resp.status_code == 200:
                print("[✓] Thumbnail uploaded successfully")
                return upload_id
            else:
                print(f"[!] Thumbnail upload failed: {resp.status_code if resp else 'No Response'}")
                return None
        except Exception as e:
            print(f"[!] Error uploading thumbnail: {e}")
            return None

    # Updated to include duration
    def _get_video_dimensions(self, video_path: str):
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', video_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for stream in data['streams']:
                    if stream['codec_type'] == 'video':
                        width = int(stream['width'])
                        height = int(stream['height'])
                        duration = float(stream.get('duration', 0))
                        return width, height, duration
        except Exception as e:
            print(f"[!] Error getting video info: {e}")
        return 720, 1280, 0  # Fallback values

    # Video upload unchanged, but added duration in params
    def _upload_video_file_improved(self, video_path: str, upload_id: str) -> bool:
        try:
            with open(video_path, 'rb') as f:
                video_data = f.read()

            video_size = len(video_data)
            width, height, duration = self._get_video_dimensions(video_path)

            entity_name = f"fb_uploader_{upload_id}"
            url = f"{self.BASE_URL}/rupload_igvideo/{entity_name}"

            headers = self._prepare_headers()
            headers.update({
                "Content-Type": "application/octet-stream",
                "Content-Length": str(video_size),
                "X-Entity-Name": entity_name,
                "X-Entity-Length": str(video_size),
                "X-Entity-Type": "video/mp4",
                "Offset": "0",
                "X-Instagram-Rupload-Params": json.dumps({
                    "media_type": 2,
                    "upload_id": upload_id,
                    "upload_media_width": width,
                    "upload_media_height": height,
                    "upload_media_duration_ms": int(duration * 1000),
                    "is_clips_video": True,
                }, separators=(',', ':'))
            })

            print(f"[*] Uploading video ({video_size / (1024 * 1024):.1f}MB, {width}x{height})...")
            resp = self._request("POST", url, headers=headers, data=video_data, timeout=120)

            if resp and resp.status_code == 200:
                print("[✓] Video uploaded successfully")
                return True
            else:
                print(f"[!] Video upload failed: {resp.status_code if resp else 'No Response'}")
                return False
        except Exception as e:
            print(f"[!] Error uploading video: {e}")
            return False

    # Modified to always use poster_frame_index, remove cover_upload_id linking
    def _configure_reel(self, upload_id: str, caption: str, width: int, height: int, duration: float) -> bool:
        """Configures the reel with a more robust payload and correct error checking."""
        try:
            print("[*] Configuring reel...")

            csrf_token = self.session.cookies.get('csrftoken', '')
            if not csrf_token:
                print("[!] Missing CSRF token for configure step.")
                return False

            config_payload = {
                'upload_id': upload_id,
                'caption': caption,
                'source_type': '4',  # Library/camera upload
                'share_to_feed': '1',
                'audience': 'public',
                'disable_comments': '0',
                'like_and_view_counts_disabled': '0',
                'only_me': '0',  # Added for public visibility
                'device_timestamp': upload_id,
                'creation_logger_session_id': str(uuid.uuid4()),
                'story_media_creation_date': str(int(time.time()) - random.randint(11, 20)),
                'client_shared_at': str(int(time.time()) - random.randint(3, 10)),
                'client_timestamp': str(int(time.time())),
                'length': duration,
                'clips_length': duration,  # Added alias for length
                'clips_metadata': json.dumps({  # Expanded from 'clips'
                    'clips_segments': [{'length': duration, 'source_type': '4'}],
                    'audio_type': 'original',  # Assumes original audio; change if muted
                    'camera_position': 'unknown',
                    'effect_id': '0',
                    'filter_id': '0'
                }),
                'audio_metadata': '{}',  # Empty for no music/edits; use json.dumps if adding data
                'audio_muted': False,
                'filter_type': '0',
                'poster_frame_index': '0',
                'video_result': 'deprecated',
                'is_clips_video': '1',  # Added to explicitly flag as Reel
                'product_type': 'clips',  # Added for Reel type
                'workflow': 'clips',  # Added for upload workflow
                'mas_opt_in': 'enabled',  # Added for media analytics
                'timezone_offset': '0',  # Added; adjust to your timezone (e.g., '3600' for UTC+1)
                'video_subtitles_enabled': '0',  # Added; no subtitles
                'video_subtitles_locale': 'en_US',  # Added; default locale
                'device': {  # Customize to match your _prepare_headers device
                    'manufacturer': 'Samsung',
                    'model': 'SM-G960F',
                    'android_version': 28,
                    'android_release': '9.0'
                },
                'extra': json.dumps({
                    'source_width': width,
                    'source_height': height
                })
            }

            config_url = f"{self.BASE_URL}/api/v1/media/configure_to_clips/"
            config_headers = self._prepare_headers()
            config_headers['Content-Type'] = 'application/x-www-form-urlencoded'  # Ensure form-encoded if not already

            resp = self._request("POST", config_url, data=config_payload, headers=config_headers)

            if not resp:
                print("[!] No response from configure endpoint.")
                return False

            print(f"[*] Configure response: {resp.status_code}")

            if resp.status_code == 200:
                try:
                    response_data = resp.json()
                    print(f"[*] Configure response data: {response_data}")

                    if response_data.get("status") == "ok" and response_data.get("message") != "media_needs_reupload":
                        print("[✓] Reel posted successfully!")
                        return True
                    else:
                        error_title = response_data.get('error_title', 'unknown error')
                        print(f"[!] Configure failed. Instagram says: '{error_title}'")
                        return False
                except json.JSONDecodeError:
                    print(f"[!] Failed to decode JSON from configure response: {resp.text[:200]}")
                    return False
            else:
                print(f"[!] Configure failed with status {resp.status_code}: {resp.text[:300]}")
                return False

        except Exception as e:
            print(f"[!] Error configuring reel: {e}")
            return False

    # Rewritten main function with changed order: upload video first, then thumbnail with same upload_id
    def upload_reel(self, video_path: str, thumbnail_path: Optional[str] = None, caption: str = "") -> bool:
        """
        Uploads a reel with an optional custom thumbnail.

        This function follows the correct flow:
        1. Upload the video file.
        2. If no thumbnail provided, extract one from the video using ffmpeg.
        3. Upload the thumbnail image using the video's upload_id to associate it as cover.
        4. Configure the reel, using poster_frame_index without explicit cover linking.
        """
        try:
            if not os.path.exists(video_path):
                print(f"[!] Video file not found: {video_path}")
                return False

            # --- Step 1: Upload Video ---
            video_upload_id = str(int(time.time() * 1000))
            if not self._upload_video_file_improved(video_path, video_upload_id):
                print("[!] Halting process due to video upload failure.")
                return False

            time.sleep(5)  # Allow time for processing

            # --- Step 2: Prepare Thumbnail ---
            created_thumbnail = False
            if thumbnail_path is None:
                # Auto-extract thumbnail if not provided
                thumbnail_path = os.path.splitext(video_path)[0] + '_thumb.jpg'
                width, height, _ = self._get_video_dimensions(video_path)
                cmd = ['ffmpeg', '-y', '-i', video_path, '-ss', '0', '-vframes', '1', '-vf', f'scale={width}:-2',
                       thumbnail_path]
                result = subprocess.run(cmd, capture_output=True)
                if result.returncode != 0:
                    print(f"[!] Failed to extract thumbnail: {result.stderr.decode()}")
                    return False
                created_thumbnail = True
                print(f"[*] Extracted thumbnail to {thumbnail_path}")
            elif not os.path.exists(thumbnail_path):
                print(f"[!] Thumbnail file not found: {thumbnail_path}")
                return False

            # --- Step 3: Upload Thumbnail using video's upload_id ---
            thumbnail_upload_id = self._upload_photo(thumbnail_path, video_upload_id)
            if not thumbnail_upload_id:
                print("[!] Halting process due to thumbnail upload failure.")
                if created_thumbnail:
                    os.remove(thumbnail_path)
                return False
            time.sleep(1)  # Brief pause

            if created_thumbnail:
                os.remove(thumbnail_path)
                print(f"[*] Cleaned up temporary thumbnail {thumbnail_path}")

            # --- Step 4: Configure Reel ---
            width, height, duration = self._get_video_dimensions(video_path)
            print(f"[*] Video dimensions: {width}x{height}, duration: {duration}s")

            if self._configure_reel(video_upload_id, caption, width, height, duration):
                return True
            else:
                print("[!] All upload methods failed.")
                return False

        except Exception as e:
            print(f"[!] An unexpected error occurred in upload_reel: {e}")
            if created_thumbnail and os.path.exists(thumbnail_path):
                os.remove(thumbnail_path)
            return False

    # NOTE: The mobile API functions were removed from this flow to simplify the fix,
    # as the web API is more direct for handling custom thumbnails.

    # I not sure about this method cause i do not tested yet
    # def comment_on_story(self, story_url: str, comment: str) -> bool:
    #     """Comment on a specific story via URL (e.g.,
    #     https://www.instagram.com/stories/cristiano/1234567890123456789/). For latest, fetch stories and pick the
    #     first."""
    #
    #     try:
    #         if not self.session.cookies.get('sessionid'):
    #             print("[!] Must be logged in to comment on stories.")
    #             return False
    #
    #         username = self._extract_username(story_url)
    #         story_pk = self._extract_story_pk(story_url)
    #         if not story_pk:
    #             # Fetch all stories and comment on the latest
    #             user_info = self._get_user_info(username)
    #             if not user_info:
    #                 return False
    #             stories = self._fetch_stories(user_info.user_id, username)
    #             if not stories:
    #                 print(f"[!] No stories found for @{username}")
    #                 return False
    #             # Sort by timestamp to get latest
    #             stories.sort(key=lambda s: s.timestamp or 0, reverse=True)
    #             story_pk = stories[0].shortcode
    #             print(f"[*] Commenting on latest story PK: {story_pk}")
    #
    #         url = f"{self.I_BASE_URL}/api/v1/web/comments/{story_pk}/add/"
    #         payload = {"comment_text": comment}
    #         headers = self._prepare_headers(referer=story_url)
    #         headers["content-type"] = "application/x-www-form-urlencoded"
    #
    #         resp = self._request("POST", url, data=payload, headers=headers)
    #         if resp.status_code == 200 and "comment" in resp.json():
    #             print(f"[✓] Comment added to story {story_pk}")
    #             return True
    #         else:
    #             print(f"[!] Failed to comment on story: {resp.json()}")
    #             return False
    #     except Exception as e:
    #         print(f"[!] Error commenting on story: {e}")
    #         return False

