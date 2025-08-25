
# InstagramClient

An advanced **Instagram Media Downloader & Uploader** built in Python.  
This class lets you **download posts, reels, stories, highlights, or full profiles**, as well as **upload posts/reels** and **comment on posts** — all programmatically.

---

## ✨ Features

- 🔽 **Download**  
  - Posts (single image, video, or carousel)  
  - Reels  
  - Stories (single story via URL or all active stories)  
  - Highlights  
  - Full profile (profile pic, posts, stories, highlights)

- 🔼 **Upload**  
  - Photos with captions  
  - Reels (with optional thumbnail and captions)

- 💬 **Interact**  
  - Comment on any post by URL  
  - Comment on the latest post of a user  

- 🔒 **Authentication**  
  - Cookie persistence (auto-load & save login cookies)  
  - Automatic re-login handling  

---

## ⚙️ How It Works

1. **HTTP Session & Tokens**  
   - Uses [`httpx`](https://www.python-httpx.org/) for requests.  
   - Fetches and manages **CSRF tokens**, **LSD tokens**, and session cookies for API calls.  
   - Randomizes user agents to avoid detection.  

2. **GraphQL & REST Endpoints**  
   - Uses Instagram’s private GraphQL `doc_id`s for posts, user feeds, and highlights.  
   - Uses REST API endpoints for stories and uploads.  

3. **Media Handling**  
   - Smart filename generation (sanitized from caption/shortcode).  
   - Downloads with progress tracking.  
   - Handles both images (`.jpg`) and videos (`.mp4`).  

4. **Upload Flow**  
   - Splits into two steps:  
     - **Upload** (chunked upload of photo/video).  
     - **Configure** (finalize post or reel with caption).  

---

## 🚀 Usage Guide

### 1. Installation
```bash
git clone https://github.com/yourusername/instagram-client.git
cd instagram-client
pip install -r requirements.txt
```

### 2. Basic Example
```python
from Instagram_Client import InstagramClient

# Initialize client
client = InstagramClient()

# Login (saves cookies for future sessions)
client.login("your_username", "your_password")

# Download a post
client.download_post("https://www.instagram.com/p/SHORTCODE/")

# Download a full profile
client.download_user_profile("cristiano", max_posts=10)

# Upload a photo
client.upload_photo("path/to/photo.jpg", caption="Hello Instagram!")

# Upload a reel
client.upload_reel("path/to/video.mp4", caption="Check this out 🚀")

# Comment on a post
client.comment_on_post("https://www.instagram.com/p/SHORTCODE/", "Nice post!")

# Comment on first (latest) post of a user
client.comment_on_first_post("natgeo", "Beautiful work!")
```

### 3. Context Manager (auto-close session)
```python
with InstagramClient() as client:
    client.login("your_username", "your_password")
    client.download_story("https://www.instagram.com/stories/user/1234567890/")
```

---

## ⚠️ Notes

- Uploading/commenting uses private web APIs → these may change, so stability is not 100% guaranteed.  
- Use responsibly; Instagram may **block accounts** that abuse these APIs.  
- For private profiles, you need to be logged in with permission to view.  

---

## 📩 Contact

👨‍💻 Developed by **[Matin](https://www.instagram.com/matindevilish_boy/)**  
📷 Instagram: [@matindevilish_boy](https://www.instagram.com/matindevilish_boy/)  
